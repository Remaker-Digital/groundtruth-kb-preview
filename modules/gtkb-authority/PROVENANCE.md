# gtkb-authority: source summary

This module is original work by Remaker Digital, a DBA of VanDusen & Palmeter, LLC. Parts are
adapted from earlier GT-KB source written by Remaker Digital. The rest, including the settings
implementation, is new. The PostgreSQL schema in `src/gtkb_authority/postgresql_v1.sql` is kept as it
was in that earlier source.

A provenance check of the source ranges this module was built from found only Remaker Digital as
author and committer. That check covered the selected ranges. It is not a claim about every file GT-KB
has ever contained.

Original software is licensed under the PolyForm Noncommercial License 1.0.0 in LICENSE. Original
documentation is licensed under the Creative Commons Attribution-NonCommercial 4.0 International
license in LICENSE-DOCUMENTATION. See NOTICE.

Runtime dependencies, installed separately and not included here:

- gtkb-protocol 0.1.0, this project, PolyForm Noncommercial 1.0.0
- pydantic 2.13.3, MIT
- psycopg[binary] 3.3.4, LGPL-3.0-only (the binary wheel bundles libpq and other libraries under their
  own licenses)
- fastapi 0.139.0, MIT
- uvicorn 0.46.0, BSD-3-Clause
- tomlkit 0.13.3, MIT

Build dependency: hatchling 1.29.0, MIT.

This source is a candidate under independent review. It is not an accepted or released artifact.
