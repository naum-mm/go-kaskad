# История изменений / Changelog

## v1.0.0 — 2026-10-25

### Каскад / Kaskad

- Проект переименован из Gokaskad в «Каскад» / Kaskad.
- Новый адрес репозитория: `https://github.com/naum-mm/kaskad`.
- Обновлены заголовок и логотип панели, сообщения CLI/установщика, документация,
  команды загрузки и имя GitHub Release. В панели отображается версия `v1.0.0`.
- Сохранены дизайн BOX4ONE, автоматическая светлая/тёмная тема и мобильная вёрстка.
- Добавлен файл `VERSION`; согласованность версии с панелью и changelog проверяется
  тестами и workflow публикации.

### Обновление существующей установки

Используйте штатный `--update` по [инструкции](docs/web.ru.md#обновление-до-v100--каскад).
Пароль, правила, порт, режим и сертификаты сохраняются; миграция БД не нужна.
До обновления создаётся резервная копия.

Для совместимости сохраняются `/opt/go-kaskad`, Compose project `gokaskad`,
volume `gokaskad_worker-data`, пути сокета/БД, имена CLI-файлов, цепочки firewall,
маркеры nginx, Certbot hooks и переменные `GOKASKAD_*`. Не переименовывайте их
вручную: это может отключить существующие правила или подключить другую БД.

### English

Rename Gokaskad to Kaskad and move the repository to `naum-mm/kaskad`. Refresh
product branding, download URLs and upgrade instructions; display v1.0.0 in the
panel. Existing installation paths, storage, firewall identifiers and configuration
remain compatible. No database migration or new environment variables are required.

## v0.1.0 — 2026-10-25

Первый релиз с тегом GitHub. Существующие функции CLI, установщика, API и worker
включены без изменений.

### Оформление

- Визуальный стиль BOX4ONE: нейтральные поверхности, тонкие рамки, скругления,
  фиолетовые и жёлтые акценты.
- Автоматическая светлая и тёмная тема по настройкам устройства.
- Обновлённые таблицы, статусы, кнопки и диалог редактирования правила.
- Адаптация для мобильных экранов, видимый фокус клавиатуры и поддержка
  `prefers-reduced-motion`.
- Системный firewall остаётся отдельной нейтральной таблицей только для чтения.
- Пересобраны автономное демо и изображения панели для README.

### Обновление

- GitHub Release создаётся из проверенного коммита `main` после успешного
  workflow Web stack. Версия и описание берутся из этого файла; существующий
  релиз не перезаписывается.

- Дополнен существующий [мануал обновления](docs/web.ru.md#обновление-до-v010--дизайн-box4one)
  и английская инструкция: резервная копия, штатный `--update`, проверки сервисов
  и принудительное обновление страницы.
- Схема БД, API, правила перенаправления и эксплуатационные переменные не менялись.

### English

First tagged release. Adopts BOX4ONE styling, automatic system light/dark appearance,
refreshed tables and forms, and responsive mobile layout. Includes rebuilt demo
and screenshots plus upgrade instructions. Existing CLI, installer, API, worker,
database format and deployment settings are unchanged.
