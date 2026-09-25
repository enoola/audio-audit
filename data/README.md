# Source and Rights Manifest

`batch` mode is intentionally blocked unless each source has an approved row in
`sources.csv` whose `permitted_uses` contains `analyze`.

Required columns:

- `local_filename`
- `rights_status` beginning with `approved`
- `permitted_uses` containing `analyze`
- `permission_reference` recommended

Do not add a row until the source owner has confirmed local processing, retention,
transcript processing, and any model-training or publication rights.
