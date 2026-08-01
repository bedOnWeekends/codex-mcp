from pathlib import Path


def test_postgres_18_mount_and_host_port():
    text = Path("docker-compose.yml").read_text()
    assert '"5433:5432"' in text
    assert "postgres_data:/var/lib/postgresql" in text
    assert "/var/lib/postgresql/data" not in text


def test_alembic_src_layout_and_psycopg():
    ini = Path("alembic.ini").read_text()
    env = Path("migrations/env.py").read_text()
    assert "prepend_sys_path = %(here)s/src" in ini
    assert "postgresql+psycopg://" in ini
    assert "create_engine" in env
    assert "async_engine_from_config" not in env
