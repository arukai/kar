# NOTICE / Авторство и условия

Required Notice: Copyright 2026 Lachy (https://github.com/lachydotmcg)
Required Notice: Anyone who uses Capri's code, art, animations or any of its other assets must credit the Capri project (https://github.com/lachydotmcg/capri-desktop). Capri's art and animations are by her artist, who prefers to stay private. Parts made by others keep their own licences, listed in its README.

## Что и откуда

**KAR** — учебный голосовой помощник на Python (русский и казахский языки). У него два источника идей:

1. **Первоначальный проект Capri на Python** (kystt29, лицензия MIT) — основа: движок команд, разбор речи, окна.
   Его авторство сохранено в `LICENSE`.
2. **capri-desktop** (Lachy, https://github.com/lachydotmcg/capri-desktop, лицензия
   [PolyForm Noncommercial 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0)) — источник идей
   для новых возможностей. Код на C# **не копировался**: подходы описаны и реализованы заново на Python.
   Рисунки, анимации и персонаж Capri **не используются**.

| Возможность в KAR | Идея из capri-desktop | Файл |
|---|---|---|
| Облачный «мозг» по одному ключу: сервис определяется по началу ключа, модель выбирается из `/models` | выбор провайдера по префиксу ключа | `cloud_ai.py` |
| ИИ переводит непонятную фразу в команду | «мозг» как слой понимания команд | `cloud_ai.py`, `main.py` |
| Напоминания с датой и временем | напоминания | `reminders.py` |
| Рутины «когда я скажу X, сделай Y» | рутины | `actions.py` |
| Поиск файлов: сначала привычные папки, потом все диски; показ в Проводнике | поиск файлов | `finder.py` |
| Громкость отдельных программ | микшер | `winsys.py`, `actions.py` |
| Автозапуск (ключ `Run` + `StartupApproved`) | автозапуск | `winsys.py` |
| Диагностика микрофона (политика конфиденциальности Windows) | диагностика «ушей» | `winsys.py` |
| Игры Steam в списке программ | список установленных игр | `winsys.py` |
| Глобальные горячие клавиши (`RegisterHotKey`) | горячие клавиши | `winsys.py` |
| Медиаклавиши как запасной путь | запасной путь управления плеером | `media.py`, `winsys.py` |
| Страницы «Параметров», заряд батареи, место на диске, память, адрес в сети | «твоё ПК» из справки Capri | `pcctl.py`, `actions.py` |
| Тёмная/светлая тема, Wi-Fi и Bluetooth, извлечение флешки | управление ПК | `pcctl.py` |
| Окна: прижать, развернуть, вернуть потерянные; скриншот области, запись экрана, клип | управление окнами, Game Bar | `pcctl.py`, `actions.py` |
| Игровой режим (план электропитания) | game mode | `pcctl.py`, `actions.py` |
| Очистка временных файлов и корзины, автозапуск Windows, обновление программ (winget) | уход за ПК | `pcctl.py`, `actions.py` |
| Напоминания об отдыхе | health breaks | `pcctl.py` |
| Заметки о пользователе | «память» о пользователе | `notes.py` |
| Диктовка | диктовка | `main.py` |

## Что это значит для использования

Так как часть идей взята из проекта с лицензией **PolyForm Noncommercial**, KAR следует считать
**некоммерческим проектом**: личное, учебное, исследовательское использование — можно; продавать или использовать
в коммерческой деятельности — нельзя. Если нужна коммерческая версия, нужно либо убрать перечисленные выше
возможности, либо договориться с автором capri-desktop.

## Прочее

* Модели распознавания речи Vosk (`bundled/`, `models/`) — Alpha Cephei, Apache 2.0.
* Голоса Edge TTS — службы Microsoft; погода — Open-Meteo; Википедия — Wikimedia.
