# bash completion for feagle.
#
# Installed by the AUR package as
# /usr/share/bash-completion/completions/feagle; for a manual setup copy it to
# ~/.local/share/bash-completion/completions/feagle.
#
# The list of commands and options mirrors `feagle --help`;
# tests/test_cli_docs.py fails when something is missing.

_feagle() {
    local cur prev
    cur=${COMP_WORDS[COMP_CWORD]}
    prev=${COMP_WORDS[COMP_CWORD - 1]}

    local commands="index search status doctor setup open pick queryd indexerd bench"
    local global="--version --help"

    # The subcommand is the first word that is not an option.
    local i command=""
    for ((i = 1; i < COMP_CWORD; i++)); do
        case ${COMP_WORDS[i]} in
            -*) ;;
            *)
                command=${COMP_WORDS[i]}
                break
                ;;
        esac
    done

    if [[ -z $command ]]; then
        COMPREPLY=($(compgen -W "$commands $global" -- "$cur"))
        return 0
    fi

    case $prev in
        --mode)
            COMPREPLY=($(compgen -W "auto hybrid fts" -- "$cur"))
            return 0
            ;;
        --mrl-dim)
            COMPREPLY=($(compgen -W "128 256 512 768 1024" -- "$cur"))
            return 0
            ;;
        --launcher)
            COMPREPLY=($(compgen -W "auto fuzzel wofi rofi tofi walker stdout" -- "$cur"))
            return 0
            ;;
        --root | --corpus)
            COMPREPLY=($(compgen -d -- "$cur"))
            return 0
            ;;
        --out)
            COMPREPLY=($(compgen -f -- "$cur"))
            return 0
            ;;
    esac

    local options=""
    case $command in
        index) options="--root --full --rebuild --no-embed --backfill --help" ;;
        search) options="--limit --mode --json --pick --with-apps --help" ;;
        status) options="--json --help" ;;
        doctor) options="--json --fast --help" ;;
        setup) options="--root --model --mrl-dim --launcher --yes --no-download --no-units --no-enable --no-index --no-backup --media-images --media-video --media-audio --help" ;;
        open) options="--line --page --time --dry-run --help" ;;
        pick) options="--query --limit --files --no-apps --no-files --help" ;;
        queryd) options="--help" ;;
        indexerd) options="--root --once --poll --help" ;;
        bench) options="--files --seed --corpus --queries --repeats --limit --no-embed --keep-db --json --out --help" ;;
    esac

    if [[ $cur == -* ]]; then
        COMPREPLY=($(compgen -W "$options" -- "$cur"))
        return 0
    fi

    if [[ $command == open ]]; then
        COMPREPLY=($(compgen -f -- "$cur"))
        return 0
    fi

    COMPREPLY=()
    return 0
}

complete -o filenames -F _feagle feagle
