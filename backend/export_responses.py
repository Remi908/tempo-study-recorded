"""Export all ratings locally on the server; never publish the database to GitHub."""
import sys
from pathlib import Path
import settings
from store import Store
output=Path(sys.argv[1]) if len(sys.argv)>1 else settings.STATE/'responses.csv'
output.parent.mkdir(parents=True,exist_ok=True)
output.write_text(Store(settings.DATABASE).export_csv(),encoding='utf-8',newline='')
print('Saved '+str(output.resolve()))
