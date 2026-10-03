"""Print shell exports deriving DATABASE_URL_APP from DATABASE_URL when it is not set (managed hosts
hand out a single connection string). The user part is ignored at runtime; each persona uses cg_app_<persona>."""
from __future__ import annotations

import os
import shlex

from psycopg.conninfo import conninfo_to_dict, make_conninfo

if not os.environ.get("DATABASE_URL_APP") and os.environ.get("DATABASE_URL") and os.environ.get("CG_APP_PASSWORD"):
    info = conninfo_to_dict(os.environ["DATABASE_URL"])
    info["user"], info["password"] = "cg_app_placeholder", os.environ["CG_APP_PASSWORD"]
    print("export DATABASE_URL_APP=" + shlex.quote(make_conninfo(**info)))
