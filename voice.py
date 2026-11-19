"""Распознавание речи (Vosk офлайн + Google для точности) и синтез голоса. Языки: казахский и русский."""
import asyncio
import ctypes
import json
import logging
import math
import os
import queue
import random
import re
import shutil
import struct
import tempfile
import threading
import time
import wave

log = logging.getLogger("kar")
RATE = 16000


def _model_complete(folder):
    return os.path.isfile(os.path.join(folder, "am", "final.mdl"))


def ascii_model_path(path):
    """Vosk не открывает пути с кириллицей (ошибка «синтаксическая ошибка в имени файла») — копируем
    модель туда, где путь латиницей. Если и имя пользователя Windows русское, его папки не подходят,
    поэтому дальше пробуем общие папки C:\\ProgramData и C:\\Users\\Public."""
    if path.isascii():
        return path
    bases = [os.environ.get("LOCALAPPDATA", ""), os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
             os.environ.get("PUBLIC", r"C:\Users\Public"), tempfile.gettempdir()]
    for base in bases:
        target = os.path.join(base, "KAR", os.path.basename(path.rstrip("\\/")))
        if not base or not target.isascii():
            continue
        try:
            if not _model_complete(target):  # копия могла оборваться в прошлый раз
                shutil.rmtree(target, ignore_errors=True)
                shutil.copytree(path, target)
            log.info("Модель распознавания скопирована в %s (в исходном пути есть кириллица)", target)
            return target
        except OSError as e:
            log.info("Не удалось скопировать модель в %s: %s", target, e)
    return path


def input_devices():
    """Устройства ввода [(название, индекс)] — по одному на микрофон, как в настройках звука Windows."""
    import sounddevice as sd
    apis = sd.query_hostapis()
    mme = next((i for i, a in enumerate(apis) if "MME" in a["name"]), None)
    out, seen = [], set()
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] <= 0 or (mme is not None and d["hostapi"] != mme):
            continue
        name = d["name"].strip()
        low = name.lower()
        if "sound mapper" in low or "переназначение звуковых" in low or "первичный драйвер" in low:
            continue  # служебные «устройства по умолчанию»
        if name not in seen:
            seen.add(name)
            out.append((name, i))
    return out


def default_input_name():
    try:
        import sounddevice as sd
        return sd.query_devices(kind="input")["name"].strip()
    except Exception:
        return "—"


def resolve_device(name):
    """Индекс устройства по сохранённому названию (или None — микрофон Windows по умолчанию)."""
    if not name:
        return None
    devices = input_devices()
    for n, i in devices:
        if n == name:
            return i
    for n, i in devices:  # название могло обрезаться или чуть измениться
        if name.lower()[:20] in n.lower() or n.lower()[:20] in name.lower():
            return i
    log.info("Микрофон «%s» не найден, беру микрофон по умолчанию", name)
    return None


def _refresh_devices(sd):
    """Перечитать список звуковых устройств (PortAudio запоминает его при старте)."""
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        log.exception("Не удалось обновить список устройств")


LEVEL_SCALE = 6.0  # громкость для индикатора: обычная речь у микрофона даёт примерно половину шкалы
GATE_MAX = 0.35    # порог при самой низкой чувствительности — тише этого звук не слышен


class Listener(threading.Thread):
    """Постоянно слушает микрофон. on_final(text, audio_bytes) — законченная фраза,
    on_partial(text) — промежуточный результат (для быстрой реакции на имя).
    Микрофон можно сменить на ходу через set_device()."""

    def __init__(self, model_path, on_final, on_partial, on_error, device_name=None, gain=1.0, sensitivity=100):
        super().__init__(daemon=True)
        self.model_path = model_path
        self.on_final = on_final
        self.on_partial = on_partial
        self.on_error = on_error
        self.device_name = device_name  # None — микрофон Windows по умолчанию
        self.active_name = None         # какой микрофон реально открыт
        self.muted = threading.Event()
        self.reset_req = threading.Event()  # сбросить распознавание (отбросить эхо голоса самой KAR)
        self.switch = threading.Event()     # переоткрыть поток с другим микрофоном
        self.gain = gain                # громкость ввода: 1 — как есть, 2 — вдвое громче
        self.sensitivity = sensitivity  # 100 — слышит всё, меньше — тихие звуки (шум, фон) отбрасываются
        self.stop_event = threading.Event()  # полностью остановить поток (смена языка)
        self.level = 0.0                # текущая громкость с микрофона 0..1 — для индикатора в настройках

    def stop(self):
        self.stop_event.set()
        self.switch.set()

    def set_device(self, name):
        self.device_name = name or None
        self.switch.set()

    def gate_level(self):
        """Порог громкости 0..1, ниже которого звук считается тишиной."""
        return (100 - self.sensitivity) / 100 * GATE_MAX

    def process(self, data):
        """Усиление и шумовой порог для блока звука int16. Заодно обновляет self.level."""
        import numpy as np
        x = np.frombuffer(data, np.int16).astype(np.float32)
        if self.gain != 1.0:
            x = np.clip(x * self.gain, -32768, 32767)
        self.level = min(1.0, float(np.sqrt(np.mean(x * x))) / 32768 * LEVEL_SCALE) if len(x) else 0.0
        if self.level < self.gate_level():
            x[:] = 0
        return x.astype(np.int16).tobytes()

    def run(self):
        try:
            import sounddevice as sd
            import vosk
            vosk.SetLogLevel(-1)
            model = vosk.Model(ascii_model_path(self.model_path))
            rec = vosk.KaldiRecognizer(model, RATE)
        except Exception as e:
            log.exception("Модель распознавания не загрузилась")
            self.on_error(str(e), "model")
            return
        worked = False  # микрофон уже хоть раз открылся
        while not self.stop_event.is_set():
            self.switch.clear()
            q = queue.Queue(maxsize=300)

            def callback(indata, frames, t, status, q=q):
                try:
                    q.put_nowait(bytes(indata))
                except queue.Full:
                    pass

            device = resolve_device(self.device_name)
            try:
                stream = sd.RawInputStream(samplerate=RATE, blocksize=4000, dtype="int16", channels=1,
                                           callback=callback, device=device)
            except Exception as e:
                if device is not None:  # выбранный микрофон не открылся — пробуем микрофон по умолчанию
                    log.info("Микрофон %s не открылся (%s), беру по умолчанию", self.device_name, e)
                    self.device_name = None
                    continue
                if worked:  # микрофон уже работал и пропал (сон, гарнитуру выдернули) — ждём, пока вернётся
                    log.info("Микрофон пока не открывается (%s), пробую снова", e)
                    time.sleep(3)
                    _refresh_devices(sd)
                    continue
                log.exception("Ошибка микрофона")
                self.on_error(str(e))
                return
            try:
                self.active_name = sd.query_devices(device if device is not None else sd.default.device[0])["name"]
            except Exception:
                self.active_name = default_input_name()
            rec.Reset()
            worked = True
            try:
                if self._listen(stream, q, rec) == "stale":
                    time.sleep(1)
                    _refresh_devices(sd)  # список устройств мог поменяться — иначе откроем «мёртвый» микрофон
            except Exception as e:
                log.exception("Ошибка микрофона/распознавания")
                self.on_error(str(e))
                return

    def _listen(self, stream, q, rec):
        with stream:
            log.info("Микрофон открыт: %s", self.active_name)
            buf = bytearray()
            was_muted = False
            last_audio = time.time()
            while not self.switch.is_set() and not self.stop_event.is_set():
                try:
                    data = self.process(q.get(timeout=0.5))
                    last_audio = time.time()
                except queue.Empty:
                    # звук приходит каждые 0,25 с даже в тишине; если его нет — микрофон отвалился
                    # (сон компьютера, гарнитуру переподключили) — переоткрываем, иначе KAR «оглохнет» молча
                    if time.time() - last_audio > 3:
                        log.info("Микрофон перестал присылать звук — переоткрываю")
                        return "stale"
                    continue
                if self.muted.is_set():
                    was_muted = True
                    continue
                if was_muted or self.reset_req.is_set():
                    was_muted = False
                    self.reset_req.clear()
                    rec.Reset()
                    buf.clear()
                buf += data
                if rec.AcceptWaveform(data):
                    text = json.loads(rec.Result()).get("text", "").strip()
                    audio = bytes(buf)
                    buf.clear()
                    if text:
                        self.on_final(text, audio)
                else:
                    part = json.loads(rec.PartialResult()).get("partial", "")
                    if part:
                        self.on_partial(part)
                    if len(buf) > RATE * 2 * 25:
                        del buf[:RATE * 2 * 5]


def google_stt(audio, language="ru-RU"):
    """Повторно распознаёт фразу через Google — точнее Vosk на вопросах. None при ошибке."""
    try:
        import speech_recognition as sr
        r = sr.Recognizer()
        r.operation_timeout = 6
        return r.recognize_google(sr.AudioData(audio, RATE, 2), language=language)
    except Exception as e:
        log.info("Google STT недоступен: %s", e)
        return None


# ---------------- синтез речи ----------------

def _mci(cmd):
    buf = ctypes.create_unicode_buffer(256)
    err = ctypes.windll.winmm.mciSendStringW(cmd, buf, 255, 0)
    return err, buf.value


def play_mp3(path, stop_event, volume=100):
    """Играет wav/mp3, пока не закончится или не взведён stop_event. True — если прервали.
    Громкость голоса вшита в wav при подготовке, общая громкость Windows не меняется."""
    _mci("close kar_tts")
    wav = path.lower().endswith(".wav")
    if _mci(f'open "{path}" type {"waveaudio" if wav else "mpegvideo"} alias kar_tts')[0] != 0:
        raise RuntimeError("MCI не открыл файл")
    if not wav:
        _mci(f"setaudio kar_tts volume to {int(max(0, min(100, volume)) * 10)}")
    _mci("play kar_tts")
    interrupted = False
    try:
        while True:
            if stop_event.wait(0.05):
                interrupted = True
                _mci("stop kar_tts")
                break
            err, mode = _mci("status kar_tts mode")
            if err or mode != "playing":
                break
    finally:
        _mci("close kar_tts")
    return interrupted


def clean_for_speech(text):
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*#_`>|\[\]]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


SR = 24000


def decode(path, sr=SR):
    """Любой аудиофайл → моно float32."""
    import subprocess
    import numpy as np
    raw = subprocess.run([_ffmpeg(), "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", str(sr), "-"],
                         capture_output=True, check=True, creationflags=0x08000000).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768


def trim_silence(x, sr=SR, thr=0.012, pad=0.04):
    import numpy as np
    loud = np.nonzero(np.abs(x) > thr)[0]
    if not len(loud):
        return x[:0]
    p = int(sr * pad)
    return x[max(0, loud[0] - p):loud[-1] + p]


def write_wav(dst, x, sr=SR, volume=100):
    """Записать wav, выровняв громкость; volume 0..100 — громкость голоса KAR."""
    import numpy as np
    x = x / max(1e-6, float(np.max(np.abs(x)))) * 0.92 * max(0, min(100, volume)) / 100
    with wave.open(dst, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((x * 32767).astype(np.int16).tobytes())


TTS_CACHE = os.path.join(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()), "KAR", "tts_cache")
CACHE_MAX_TEXT = 160    # фразы длиннее не кэшируем
CACHE_MAX_FILES = 600   # сколько готовых фраз хранить

# голоса Microsoft Edge: у казахского языка их два (женский и мужской)
EDGE_VOICES = {"kk": ("kk-KZ-AigulNeural", "kk-KZ-DauletNeural"), "ru": ("ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural")}
# для окна настроек: (подпись, голос Edge)
VOICE_CHOICES = {"kk": (("Айгүл (әйел)", "kk-KZ-AigulNeural"), ("Дәулет (ер)", "kk-KZ-DauletNeural")),
                 "ru": (("Светлана (жен.)", "ru-RU-SvetlanaNeural"), ("Дмитрий (муж.)", "ru-RU-DmitryNeural"))}
SAPI_WORDS = {"kk": ("Kazakh", "Қазақ"), "ru": ("Russian", "Irina", "Pavel")}


def _trim_cache():
    try:
        files = sorted((os.path.join(TTS_CACHE, f) for f in os.listdir(TTS_CACHE)), key=os.path.getmtime)
        for f in files[:-CACHE_MAX_FILES]:
            os.remove(f)
    except OSError:
        pass


class Speaker:
    """Озвучка: Edge (онлайн, есть казахский голос) или голос Windows SAPI (офлайн)."""

    def __init__(self, cfg):
        self.stop_event = threading.Event()
        self.path = os.path.join(tempfile.gettempdir(), f"kar_tts_{os.getpid()}.mp3")
        self.wav_path = os.path.join(tempfile.gettempdir(), f"kar_tts_{os.getpid()}.wav")
        self.edge_error = None  # почему не сработал голос Edge в последний раз (None — сработал)
        self.configure(cfg)

    def configure(self, cfg):
        self.language = cfg.get("language", "kk")
        self.engine = cfg.get("tts_engine", "edge")           # edge — онлайн, sapi — офлайн
        self.voice_kk = cfg.get("voice_kk", EDGE_VOICES["kk"][0])
        self.voice_ru = cfg.get("voice_ru", EDGE_VOICES["ru"][0])
        self.wpm = int(cfg.get("voice_speed", 160))            # слов в минуту, 160 — обычный темп
        self.volume = int(cfg.get("voice_volume", 100))        # 0..100, общая громкость Windows не меняется

    @property
    def voice(self):
        return self.voice_kk if self.language == "kk" else self.voice_ru

    @property
    def rate(self):
        """Темп для Edge в процентах: 160 слов/мин = «+0%»."""
        return f"{max(-50, min(100, round((self.wpm - 160) / 160 * 100))):+d}%"

    def stop(self):
        """Перебить: можно вызывать из любого потока."""
        self.stop_event.set()

    def say(self, text):
        """Блокирует поток до конца фразы. Возвращает True, если речь перебили."""
        self.stop_event.clear()
        text = clean_for_speech(text)
        if not text:
            return False
        if self.engine == "edge":
            try:
                interrupted = self._edge(text)
                self.edge_error = None
                return interrupted
            except Exception as e:
                self.edge_error = str(e)
                log.info("edge-tts недоступен (%s), использую голос Windows", e)
        try:
            return self._sapi(text)
        except Exception:
            log.exception("SAPI не сработал")
            return False

    def render(self, text, dst):
        """Edge → mp3 → wav нужной громкости."""
        import edge_tts

        async def run():
            await edge_tts.Communicate(text, self.voice, rate=self.rate).save(self.path)
        asyncio.run(run())
        x = trim_silence(decode(self.path))
        if not len(x):
            raise RuntimeError("пустой звук")
        write_wav(dst, x, volume=self.volume)
        return dst

    def _cache_path(self, text):
        import hashlib
        key = "|".join(map(str, (text, self.engine, self.voice, self.rate, self.volume)))
        return os.path.join(TTS_CACHE, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".wav")

    def cached_render(self, text):
        """Путь к готовому звуку фразы: из кэша или свежий (и тогда кладём его в кэш)."""
        path = self._cache_path(text)
        if os.path.isfile(path):
            os.utime(path)
            return path
        out = self.render(text, self.wav_path)
        if len(text) <= CACHE_MAX_TEXT:  # длинные ответы нейросети не повторяются
            try:
                os.makedirs(TTS_CACHE, exist_ok=True)
                shutil.copyfile(out, path)
                _trim_cache()
            except OSError:
                pass
        return out

    def prewarm(self, phrases):
        """Заранее озвучить постоянные фразы в фоне — чтобы потом они звучали сразу."""
        for text in phrases:
            text = clean_for_speech(text)
            if not text or os.path.isfile(self._cache_path(text)):
                continue
            try:
                self.cached_render(text)
            except Exception as e:
                log.info("Предзагрузка фраз остановлена: %s", e)
                return
            time.sleep(0.5)

    def _edge(self, text):
        path = self.cached_render(text)
        if self.stop_event.is_set():
            return True
        return play_mp3(path, self.stop_event, self.volume)

    def _sapi(self, text):
        import comtypes.client
        sp = comtypes.client.CreateObject("SAPI.SpVoice")
        voices = sp.GetVoices()
        words = SAPI_WORDS.get(self.language, ()) + SAPI_WORDS["ru"]  # казахского офлайн-голоса обычно нет
        for w in words:
            hit = next((i for i in range(voices.Count) if w in voices.Item(i).GetDescription()), None)
            if hit is not None:
                sp.Voice = voices.Item(hit)
                break
        sp.Volume = max(0, min(100, self.volume))
        sp.Rate = max(-10, min(10, round((self.wpm - 160) / 12)))
        sp.Speak(text, 1)  # SVSFlagsAsync
        while not sp.WaitUntilDone(50):
            if self.stop_event.is_set():
                sp.Speak("", 2)  # SVSFPurgeBeforeSpeak — обрывает текущую речь
                return True
        return False
