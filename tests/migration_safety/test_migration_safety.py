import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "migration_safety", ROOT / "scripts/migration_safety.py"
)
migration_safety = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration_safety)


class MigrationSafetyTests(unittest.TestCase):
    def test_finds_destructive_operations_only_in_up_method(self):
        source = """\
protected override void Up(MigrationBuilder migrationBuilder)
{
    migrationBuilder.DropColumn(name: "Old", table: "Tasks");
    migrationBuilder.AlterColumn<int>(name: "Count", table: "Tasks");
    // migrationBuilder.DropTable("Commented");
}
protected override void Down(MigrationBuilder migrationBuilder)
{
    migrationBuilder.DropTable("DownOnly");
}
"""
        findings = migration_safety.destructive_operations(source)
        self.assertEqual(
            findings,
            [
                (3, "DropColumn", "drops column data"),
                (4, "AlterColumn", "may change the SQL type, nullability, or stored values"),
            ],
        )

    def test_ignores_designer_and_snapshot_files_when_selecting_migrations(self):
        ids = migration_safety._migration_ids(
            [
                "src/TaskManagement.Api/Migrations/20260929112233_AddStatus.cs",
                "src/TaskManagement.Api/Migrations/20260929112233_AddStatus.Designer.cs",
                "src/TaskManagement.Api/Migrations/TaskManagementDbContextModelSnapshot.cs",
                "src/TaskManagement.Api/Data/20260929112233_NotAMigration.cs",
            ]
        )
        self.assertEqual(ids, ["20260929112233_AddStatus"])

    def test_added_migrations_are_compared_to_the_trusted_base(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            migrations = root / migration_safety.MIGRATIONS_DIR
            migrations.mkdir(parents=True)
            (migrations / "20260924154808_InitialAzureSql.cs").write_text("initial", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "-qm",
                    "Base",
                ],
                cwd=root,
                check=True,
            )
            base_sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip()
            (migrations / "20260929112233_AddStatus.cs").write_text("new", encoding="utf-8")
            (migrations / "20260929112233_AddStatus.Designer.cs").write_text("metadata", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "-qm",
                    "Add migration",
                ],
                cwd=root,
                check=True,
            )

            self.assertEqual(
                migration_safety.added_migration_ids(base_sha, root=root),
                ["20260929112233_AddStatus"],
            )
            self.assertEqual(
                migration_safety.latest_base_migration(base_sha, root=root),
                "20260924154808_InitialAzureSql",
            )

    def test_rejects_a_non_full_base_revision(self):
        with self.assertRaises(migration_safety.MigrationSafetyError):
            migration_safety.added_migration_ids("main")

    def test_workflow_is_path_scoped_read_only_and_uses_the_trusted_base(self):
        workflow = (ROOT / ".github/workflows/migration-safety.yml").read_text(encoding="utf-8")
        design_time_factory = (
            ROOT / "src/TaskManagement.Api/Data/TaskManagementDesignTimeDbContextFactory.cs"
        ).read_text(encoding="utf-8")
        self.assertIn("pull_request:", workflow)
        self.assertIn("src/TaskManagement.Api/Migrations/**", workflow)
        self.assertIn("src/TaskManagement.Api/Data/**", workflow)
        self.assertIn("src/TaskManagement.Api/Program.cs", workflow)
        self.assertIn("permissions:\n  contents: read\n", workflow)
        self.assertIn("github.event.pull_request.base.sha", workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("--idempotent", workflow)
        self.assertIn("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", workflow)
        self.assertIn("actions/setup-dotnet@a98b56852c35b8e3190ac28c8c2271da59106c68", workflow)
        self.assertIn("actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a", workflow)
        self.assertIn("migration_safety.py report", workflow)
        self.assertIn("127.0.0.1,1", design_time_factory)
        self.assertNotIn("pull_request_target:", workflow)
        self.assertNotIn("secrets.", workflow)


if __name__ == "__main__":
    unittest.main()
