from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_compose_publishes_nothing_beyond_loopback():
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    assert "ports" not in services["qdrant"]  # vector store is internal to the compose network
    for spec in services["api"]["ports"]:
        assert str(spec).startswith("127.0.0.1:"), spec


AZURE = ROOT / "infra" / "azure"


def test_azure_template_keeps_secrets_out_of_tracked_files():
    template = (AZURE / "main.bicep").read_text(encoding="utf-8")
    params = (AZURE / "main.bicepparam").read_text(encoding="utf-8")
    # The token is a secure parameter, read from the environment by the parameters file, stored in Key Vault and
    # handed to the app as a Key Vault secret reference.
    assert "@secure()\n@minLength(32)\nparam apiToken string" in template
    assert "param apiToken = readEnvironmentVariable('CODEATLAS_API_TOKEN')" in params
    assert "keyVaultUrl: apiTokenSecret.properties.secretUri" in template
    assert "{ name: 'CODEATLAS_API_TOKEN', secretRef: 'api-token' }" in template
    # Every value in the parameters file comes from the environment: no email address, name or id is committed.
    for line in params.splitlines():
        if line.startswith("param "):
            assert "readEnvironmentVariable(" in line, line
    # Entra ID only in the cloud: the app gets no API key, and the registry has no admin password.
    assert "{ name: 'CODEATLAS_AZURE_OPENAI_AUTH', value: 'entra' }" in template
    assert "AZURE_OPENAI_API_KEY" not in template and "adminUserEnabled: false" in template


def test_azure_template_defaults():
    template = (AZURE / "main.bicep").read_text(encoding="utf-8")
    assert "allowInsecure: false" in template  # HTTPS only: the UI sends the API token
    assert "param defaultSetup string = 'fast'" in template
    assert "param minReplicas int = 0" in template  # scale to zero allowed
    assert "param budgetAmount int = 10" in template
    assert "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd" in (AZURE / "modules" / "openai-user.bicep").read_text(encoding="utf-8")
