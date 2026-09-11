from app.migrations import _split_mysql


def test_split_mysql_ignores_semicolons_in_comments_and_literals():
    script = """-- migration note; keep this comment intact
START TRANSACTION;
INSERT INTO sample(value) VALUES ('literal; value'); /* block; note */
COMMIT;
"""

    statements = _split_mysql(script)

    assert len(statements) == 3
    assert statements[0].endswith("START TRANSACTION")
    assert "'literal; value'" in statements[1]
    assert "/* block; note */" in statements[2]
    assert statements[2].endswith("COMMIT")
