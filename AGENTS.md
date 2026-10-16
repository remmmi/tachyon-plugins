# AGENTS.md

This repository holds one folder per Tachyon plugin. Each folder is the plugin itself (its files at the top, as in
the core's own `plugins/`), with, when the plugin needs more than a copy into Tachyon, an `AGENTS.md`, a `deploy/`
kit and `docs/` next to the plugin files.

- To install a plugin, read **its** `AGENTS.md` (for example `external-search/AGENTS.md`) and follow it; paths there
  are relative to that plugin's folder.
- A plugin without an `AGENTS.md` installs by copying its folder (or its release zip) into Tachyon's
  `_data_/_default_/plugins/`, then enabling it in *Admin → Extensions*; its `README.md` lists the settings.
- Clone this repository outside Tachyon's data folder: Tachyon replaces a plugin's folder when the plugin is
  reinstalled from the admin panel, and a `deploy/` kit keeps keys and data.
- Never modify the code of Tachyon itself; everything goes through plugins, their settings and `application.ini`.
