# Source this file from an interactive zsh. Paths resolve on both Apple Silicon and Intel.
[[ -o interactive ]] || return 0

typeset -g ITERM2_PORTABLE_DIR="${${(%):-%N}:A:h}"
typeset -g ITERM2_BREW_PREFIX
if (( $+commands[brew] )); then
  ITERM2_BREW_PREFIX="$(brew --prefix)"
elif [[ -d /opt/homebrew/share/powerlevel10k ]]; then
  ITERM2_BREW_PREFIX=/opt/homebrew
elif [[ -d /usr/local/share/powerlevel10k ]]; then
  ITERM2_BREW_PREFIX=/usr/local
else
  print -u2 'iterm2-portable: install Homebrew and the documented shell dependencies.'
  return 1
fi

export COLORTERM=truecolor
export CLICOLOR=1
export LSCOLORS='Gxfxcxdxbxegedabagacad'
export LS_COLORS='di=38;5;25:ln=38;5;61:so=38;5;29:pi=38;5;136:ex=38;5;124:bd=38;5;25;48;5;153:cd=38;5;25;48;5;229:su=38;5;15;48;5;124:sg=38;5;15;48;5;37:tw=38;5;15;48;5;64:ow=38;5;15;48;5;136'
export LESS_TERMCAP_md=$'\e[1;38;5;25m'
export LESS_TERMCAP_me=$'\e[0m'
export LESS_TERMCAP_so=$'\e[48;5;153;38;5;24m'
export LESS_TERMCAP_se=$'\e[0m'
export LESS_TERMCAP_us=$'\e[4;38;5;31m'
export LESS_TERMCAP_ue=$'\e[0m'

bindkey -e
bindkey '^?' backward-delete-char
bindkey '^[3~' delete-char
setopt NO_BEEP PROMPT_SUBST

typeset -U fpath
fpath=("$ITERM2_BREW_PREFIX/share/zsh/site-functions" $fpath)
if (( ! $+functions[compdef] )); then
  autoload -Uz compinit
  compinit
fi
zstyle ':completion:*' menu select
zstyle ':completion:*' matcher-list 'm:{a-z}={A-Za-z}'
zstyle ':completion:*' list-colors "${(s.:.)LS_COLORS}"
zstyle ':completion:*' squeeze-slashes true
zstyle ':completion:*' use-cache on
zstyle ':completion:*' cache-path "${XDG_CACHE_HOME:-$HOME/.cache}/zsh"
zstyle ':completion:*' group-name ''
zstyle ':completion:*:descriptions' format '%F{61}-- %d --%f'
zstyle ':completion:*:warnings' format '%F{160}no matches for:%f %d'
zstyle ':completion:*:corrections' format '%F{130}%d (errors: %e)%f'
autoload -Uz colors && colors

if [[ -r "$ITERM2_BREW_PREFIX/share/zsh-autosuggestions/zsh-autosuggestions.zsh" ]]; then
  source "$ITERM2_BREW_PREFIX/share/zsh-autosuggestions/zsh-autosuggestions.zsh"
  ZSH_AUTOSUGGEST_STRATEGY=(history completion)
  ZSH_AUTOSUGGEST_USE_ASYNC=1
  ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE='fg=245'
fi

if [[ -r "$ITERM2_BREW_PREFIX/share/powerlevel10k/powerlevel10k.zsh-theme" ]]; then
  source "$ITERM2_BREW_PREFIX/share/powerlevel10k/powerlevel10k.zsh-theme"
  source "$ITERM2_PORTABLE_DIR/p10k.zsh"
else
  print -u2 'iterm2-portable: powerlevel10k is missing; run the installer with --install-deps.'
fi

if [[ -r "$ITERM2_BREW_PREFIX/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh" ]]; then
  export ZSH_HIGHLIGHT_HIGHLIGHTERS_DIR="$ITERM2_BREW_PREFIX/share/zsh-syntax-highlighting/highlighters"
  typeset -gA ZSH_HIGHLIGHT_STYLES
  ZSH_HIGHLIGHT_STYLES[default]='fg=238'
  ZSH_HIGHLIGHT_STYLES[unknown-token]='fg=160,bold'
  ZSH_HIGHLIGHT_STYLES[reserved-word]='fg=61,bold'
  ZSH_HIGHLIGHT_STYLES[alias]='fg=25'
  ZSH_HIGHLIGHT_STYLES[builtin]='fg=31'
  ZSH_HIGHLIGHT_STYLES[function]='fg=25'
  ZSH_HIGHLIGHT_STYLES[command]='fg=31'
  ZSH_HIGHLIGHT_STYLES[path]='fg=24,underline'
  ZSH_HIGHLIGHT_STYLES[path_pathseparator]='fg=67'
  ZSH_HIGHLIGHT_STYLES[globbing]='fg=60'
  ZSH_HIGHLIGHT_STYLES[single-quoted-argument]='fg=64'
  ZSH_HIGHLIGHT_STYLES[double-quoted-argument]='fg=64'
  ZSH_HIGHLIGHT_STYLES[dollar-quoted-argument]='fg=64'
  ZSH_HIGHLIGHT_STYLES[comment]='fg=247,italic'
  ZSH_HIGHLIGHT_STYLES[option]='fg=130'
  ZSH_HIGHLIGHT_STYLES[arg0]='fg=31'
  source "$ITERM2_BREW_PREFIX/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh"
fi
