"""Row-Level Security: the authoritative, fail-closed enforcement of Rule 1.

Every scoped table only exposes rows whose local matches the per-request setting
`app.current_local`. When that setting is unset, current_setting(..., true) is NULL
and the policy matches zero rows — so a forgotten filter (or a brand-new endpoint
that forgets to scope) leaks nothing; the database refuses.

RLS applies to the non-owner app role the runtime connects as. The owner/superuser
role used for migrations and seeding bypasses RLS by design.
"""
from django.conf import settings
from django.db import migrations

# table -> the column compared against the current local
SCOPED = {
    "crewlink_local": "id",
    "crewlink_member": "local_id",
    "crewlink_announcement": "local_id",
    "crewlink_announcementrecipient": "local_id",
}


def _forwards():
    stmts = []
    for table, col in SCOPED.items():
        stmts.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        stmts.append(f"DROP POLICY IF EXISTS local_isolation ON {table};")
        stmts.append(
            f"CREATE POLICY local_isolation ON {table} "
            f"USING ({col} = current_setting('app.current_local', true)::uuid) "
            f"WITH CHECK ({col} = current_setting('app.current_local', true)::uuid);"
        )
    return "\n".join(stmts)


def _backwards():
    stmts = []
    for table in SCOPED:
        stmts.append(f"DROP POLICY IF EXISTS local_isolation ON {table};")
        stmts.append(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
    return "\n".join(stmts)


class Migration(migrations.Migration):
    dependencies = [("crewlink", "0001_initial")]

    operations = [migrations.RunSQL(_forwards(), _backwards())]
