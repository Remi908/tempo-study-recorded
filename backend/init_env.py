"""Create a local .env without overwriting existing configuration."""
import secrets
from pathlib import Path
root=Path(__file__).resolve().parent
target=root/'.env'
if target.exists():
    raise SystemExit('backend/.env already exists. Edit it directly; no file was overwritten.')
text=(root/'.env.example').read_text().replace('GENERATE_WITH_INIT_ENV',secrets.token_urlsafe(40))
with target.open('x') as file:
    file.write(text)
target.chmod(0o600)
print('Created backend/.env. Edit the audio folder and evaluator paths before starting.')
