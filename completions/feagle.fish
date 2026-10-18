# fish completion for feagle.
#
# Installed by the AUR package as
# /usr/share/fish/vendor_completions.d/feagle.fish; for a manual setup copy it to
# ~/.config/fish/completions/feagle.fish.
#
# The list of commands and options mirrors `feagle --help`;
# tests/test_cli_docs.py fails when something is missing.

function __fish_feagle_needs_command
    test (count (commandline -opc)) -eq 1
end

function __fish_feagle_using_command
    set -l words (commandline -opc)
    test (count $words) -ge 2; and test $words[2] = $argv[1]
end

# No file completion by default; the commands and their arguments say what fits.
complete -c feagle -f

complete -c feagle -n __fish_feagle_needs_command -l version -d 'Print the version and exit'
complete -c feagle -n __fish_feagle_needs_command -s h -l help -d 'Show help'

complete -c feagle -n __fish_feagle_needs_command -a index -d 'Scan and index the configured roots'
complete -c feagle -n __fish_feagle_needs_command -a search -d 'Search the index'
complete -c feagle -n __fish_feagle_needs_command -a status -d 'Show index statistics'
complete -c feagle -n __fish_feagle_needs_command -a doctor -d 'Check the environment and dependencies'
complete -c feagle -n __fish_feagle_needs_command -a setup -d 'First-run interactive setup'
complete -c feagle -n __fish_feagle_needs_command -a open -d 'Open a file at a line or page'
complete -c feagle -n __fish_feagle_needs_command -a pick -d 'Open a launcher with applications and indexed files'
complete -c feagle -n __fish_feagle_needs_command -a queryd -d 'Run the query daemon in the foreground'
complete -c feagle -n __fish_feagle_needs_command -a indexerd -d 'Run the indexing daemon in the foreground'
complete -c feagle -n __fish_feagle_needs_command -a bench -d 'Run synthetic indexing and query benchmarks'

# -- index
complete -c feagle -n '__fish_feagle_using_command index' -l root -r -a '(__fish_complete_directories)' -d 'Index this directory instead of the configured roots'
complete -c feagle -n '__fish_feagle_using_command index' -l full -d 'Re-extract every file'
complete -c feagle -n '__fish_feagle_using_command index' -l rebuild -d 'Wipe the index and rebuild it'
complete -c feagle -n '__fish_feagle_using_command index' -l no-embed -d 'Full-text indexing only'
complete -c feagle -n '__fish_feagle_using_command index' -l backfill -d 'Embed files that still lack vectors'
complete -c feagle -n '__fish_feagle_using_command index' -s h -l help -d 'Show help'

# -- search
complete -c feagle -n '__fish_feagle_using_command search' -l limit -r -d 'Maximum number of results'
complete -c feagle -n '__fish_feagle_using_command search' -l mode -r -a 'auto hybrid fts' -d 'Search mode'
complete -c feagle -n '__fish_feagle_using_command search' -l json -d 'Emit JSON'
complete -c feagle -n '__fish_feagle_using_command search' -l pick -d 'Choose a result in a launcher'
complete -c feagle -n '__fish_feagle_using_command search' -l with-apps -d 'Also list desktop applications'
complete -c feagle -n '__fish_feagle_using_command search' -s h -l help -d 'Show help'

# -- status
complete -c feagle -n '__fish_feagle_using_command status' -l json -d 'Emit JSON'
complete -c feagle -n '__fish_feagle_using_command status' -s h -l help -d 'Show help'

# -- doctor
complete -c feagle -n '__fish_feagle_using_command doctor' -l json -d 'Emit JSON'
complete -c feagle -n '__fish_feagle_using_command doctor' -l fast -d 'Skip hashing the model file'
complete -c feagle -n '__fish_feagle_using_command doctor' -s h -l help -d 'Show help'

# -- setup
complete -c feagle -n '__fish_feagle_using_command setup' -l root -r -a '(__fish_complete_directories)' -d 'Directories to index'
complete -c feagle -n '__fish_feagle_using_command setup' -l model -r -d 'Model preset or GGUF path'
complete -c feagle -n '__fish_feagle_using_command setup' -l mrl-dim -r -a '128 256 512 768 1024' -d 'Truncate embeddings to N dimensions'
complete -c feagle -n '__fish_feagle_using_command setup' -l launcher -r -a 'auto fuzzel wofi rofi tofi walker stdout' -d 'Launcher for feagle pick'
complete -c feagle -n '__fish_feagle_using_command setup' -l yes -d 'Accept defaults'
complete -c feagle -n '__fish_feagle_using_command setup' -l no-download -d 'Skip the model download'
complete -c feagle -n '__fish_feagle_using_command setup' -l no-units -d 'Skip the systemd units'
complete -c feagle -n '__fish_feagle_using_command setup' -l no-enable -d 'Install the units but do not start them'
complete -c feagle -n '__fish_feagle_using_command setup' -l no-index -d 'Skip the first index'
complete -c feagle -n '__fish_feagle_using_command setup' -l no-backup -d 'Do not back up the config'
complete -c feagle -n '__fish_feagle_using_command setup' -l media-images -d 'Enable image search'
complete -c feagle -n '__fish_feagle_using_command setup' -l media-video -d 'Enable video frame indexing'
complete -c feagle -n '__fish_feagle_using_command setup' -l media-audio -d 'Enable audio transcription'
complete -c feagle -n '__fish_feagle_using_command setup' -s h -l help -d 'Show help'

# -- open
complete -c feagle -n '__fish_feagle_using_command open' -l line -r -d 'Line number'
complete -c feagle -n '__fish_feagle_using_command open' -l page -r -d 'Page number'
complete -c feagle -n '__fish_feagle_using_command open' -l time -r -d 'Start time in seconds'
complete -c feagle -n '__fish_feagle_using_command open' -l dry-run -d 'Print the command instead of running it'
complete -c feagle -n '__fish_feagle_using_command open' -s h -l help -d 'Show help'
complete -c feagle -n '__fish_feagle_using_command open' -F

# -- pick
complete -c feagle -n '__fish_feagle_using_command pick' -l query -r -d 'Rank the files for this query'
complete -c feagle -n '__fish_feagle_using_command pick' -l limit -r -d 'Number of results'
complete -c feagle -n '__fish_feagle_using_command pick' -l files -r -d 'How many indexed files to list'
complete -c feagle -n '__fish_feagle_using_command pick' -l no-apps -d 'Only list files'
complete -c feagle -n '__fish_feagle_using_command pick' -l no-files -d 'Only list applications'
complete -c feagle -n '__fish_feagle_using_command pick' -s h -l help -d 'Show help'

# -- queryd
complete -c feagle -n '__fish_feagle_using_command queryd' -s h -l help -d 'Show help'

# -- indexerd
complete -c feagle -n '__fish_feagle_using_command indexerd' -l root -r -a '(__fish_complete_directories)' -d 'Watch this directory instead of the configured roots'
complete -c feagle -n '__fish_feagle_using_command indexerd' -l once -d 'Do a single pass and exit'
complete -c feagle -n '__fish_feagle_using_command indexerd' -l poll -d 'Poll instead of using inotify'
complete -c feagle -n '__fish_feagle_using_command indexerd' -s h -l help -d 'Show help'

# -- bench
complete -c feagle -n '__fish_feagle_using_command bench' -l files -r -d 'Documents to generate'
complete -c feagle -n '__fish_feagle_using_command bench' -l seed -r -d 'Random seed'
complete -c feagle -n '__fish_feagle_using_command bench' -l corpus -r -a '(__fish_complete_directories)' -d 'Corpus directory'
complete -c feagle -n '__fish_feagle_using_command bench' -l queries -r -d 'How many queries to time'
complete -c feagle -n '__fish_feagle_using_command bench' -l repeats -r -d 'Timing repetitions'
complete -c feagle -n '__fish_feagle_using_command bench' -l limit -r -d 'Results per query'
complete -c feagle -n '__fish_feagle_using_command bench' -l no-embed -d 'Full-text only'
complete -c feagle -n '__fish_feagle_using_command bench' -l keep-db -d 'Keep the throwaway database'
complete -c feagle -n '__fish_feagle_using_command bench' -l json -d 'Print JSON'
complete -c feagle -n '__fish_feagle_using_command bench' -l out -r -a '(__fish_complete_path)' -d 'Write the result to a file'
complete -c feagle -n '__fish_feagle_using_command bench' -s h -l help -d 'Show help'
