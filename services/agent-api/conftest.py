"""Select an isolated database BEFORE application modules construct their engines."""

import os
import tempfile

from sqlalchemy.engine import make_url

from agent_api.config import get_settings

business = make_url(get_settings().database_url)
test = make_url(
    os.environ.get("TEST_DATABASE_URL")
    or business.set(database=f"{business.database}_test").render_as_string(hide_password=False)
)
if not test.database or not test.database.endswith("_test") or test == business:
    raise RuntimeError("Tests require a separate TEST_DATABASE_URL ending in _test")
os.environ["DATABASE_URL"] = test.render_as_string(hide_password=False)
# API tests must not create/delete files under the deployed upload directory.
_uploads = tempfile.TemporaryDirectory(prefix="agentos-test-uploads-")
os.environ["UPLOAD_ROOT"] = _uploads.name
os.environ["KNOWLEDGE_SOURCE_ROOT"] = _uploads.name + "/knowledge"
get_settings.cache_clear()

# Pooled asyncpg connections cannot cross pytest's event loops.
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from agent_api.db import session as test_sessions  # noqa: E402

test_sessions.engine = create_async_engine(test, poolclass=NullPool)
test_sessions.session_factory.configure(bind=test_sessions.engine)
