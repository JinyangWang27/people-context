# Reproducible README demo. Render with: vhs docs/assets/demo.tape
# Requires `uv` on PATH; runs entirely against the isolated fictional demo database.
Output docs/assets/demo.gif

Set Shell "bash"
Set FontSize 15
Set Width 1000
Set Height 620
Set Theme "Catppuccin Mocha"
Set Padding 18
Set TypingSpeed 40ms
Set PlaybackSpeed 1.0

Hide
Type `export XDG_DATA_HOME=$(mktemp -d) PS1='$ '; pctx() { uvx --from people-context pctx "$@"; }; uvx --from people-context pctx --help >/dev/null 2>&1; clear`
Enter
Show

Type "uvx --from people-context pctx demo --reset"
Sleep 400ms
Enter
Sleep 3s

Type "clear"
Enter
Type "pctx --db $XDG_DATA_HOME/people-context/demo.db list"
Sleep 300ms
Enter
Sleep 3s

Type "clear"
Enter
Type 'pctx --db $XDG_DATA_HOME/people-context/demo.db show "Amina Hassan"'
Sleep 300ms
Enter
Sleep 5s
