# Records the README demo:  adjlint  demo.gif
#
# Needs the `vhs` terminal-recording tool (from Charm) on PATH, plus adjlint
# installed in that environment (`pip install -e .`). Run from repo root:
#
#     vhs demo.tape
#
# The commands below are the real thing, against the committed example data, so
# the GIF cannot drift from what a first-time user sees.

Set Shell "bash"
Set Theme "Tokyo Night"
Set Width 1080
Set Height 420
Set FontSize 15
Set TypeRate 40
Set Padding 24 28

# 1. the report a user gets on first contact
Type "adjlint audit -p examples/prices.csv -a examples/actions.csv --profile tse"
Enter
Sleep 2.2

# 2. the same report as a Markdown table, which is what lands in a PR
Type "clear"
Enter
Type "adjlint audit -p examples/prices.csv -a examples/actions.csv --profile tse --format md | head -14"
Enter
Sleep 2.2

# 3. the catalog, so a viewer knows what they are getting
Type "clear"
Enter
Type "adjlint rules"
Enter
Sleep 2.0
