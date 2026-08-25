with open('README.md', 'r') as f:
    content = f.read()

old = '''- agent/geotiff.py currently rejects any GPS coordinate outside a
  hardcoded Portugal mainland range -- needs to become configurable
  before installing on a rack outside Portugal.'''
new = '''- agent/geotiff.py is intentionally minimal today: it unzips the
  archive, uploads the first .tif/.tiff it finds as media_type=GEOTIFF,
  and relies entirely on GetCondor's server-side TiTiler processing to
  derive geographic bounds on upload. It does not extract GPS, capture
  time, or sensor type from the file itself, and does not validate
  coordinates against any region -- there is currently no
  Portugal-specific restriction to remove.'''

assert old in content, "pattern not found"
content = content.replace(old, new, 1)

with open('README.md', 'w') as f:
    f.write(content)
print("OK: README.md - geotiff.py limitation corrected to match actual code")
