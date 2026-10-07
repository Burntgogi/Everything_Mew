"""Everything query composition and broadness helpers."""

from __future__ import annotations

import ntpath
import re
from pathlib import PureWindowsPath
from typing import NamedTuple, TypeAlias

POSITIVE_FILTER_PATTERN = re.compile(r"^(?:path:|ext:|dm:|dc:|rc:|size:)", re.IGNORECASE)
DRIVE_ROOT_PATTERN = re.compile(r"^[a-z]:[\\/]*$", re.IGNORECASE)
UNC_ROOT_PATTERN = re.compile(r"^[\\/]{2}[^\\/]+(?:[\\/]+[^\\/]+)?[\\/]*$")
CONTENT_FUNCTIONS = frozenset(
    {
        "content",
        "ansicontent",
        "contenta",
        "asciicontent",
        "binarycontent",
        "bytestreamcontent",
        "octetstreamcontent",
        "utf8content",
        "utf16content",
        "utf16becontent",
        "alternatedatastreamansi",
        "adsansi",
        "alternatedatastreamhex",
        "adshex",
        "alternatedatastreamtextplain",
        "adstextplain",
        "alternatedatastreamutf16",
        "adsutf16",
        "alternatedatastreamutf16be",
        "adsutf16be",
        "alternatedatastreamutf8",
        "adsutf8",
    }
)
FORCED_DISK_MODIFIERS = frozenset({"fromdisk"})
REGEX_FUNCTIONS = frozenset({"regex"})
WILDCARD_FUNCTIONS = frozenset({"wildcards"})
FUNCTION_CHAIN_IDENTIFIER_PATTERN = re.compile(r"^\??[A-Za-z][A-Za-z0-9_-]*\*?$")
NARROWING_FUNCTIONS = frozenset(
    {
        "path",
        "parent",
        "infolder",
        "nosubfolders",
        "ext",
        "dm",
        "datemodified",
        "dc",
        "datecreated",
        "da",
        "dateaccessed",
        "rc",
        "recentchange",
        "size",
    }
)
PREFIX_MODIFIERS = frozenset(
    {
        "ascii",
        "utf8",
        "noascii",
        "case",
        "nocase",
        "diacritics",
        "nodiacritics",
        "file",
        "files",
        "nofileonly",
        "folder",
        "folders",
        "nofolderonly",
        "nopath",
        "noregex",
        "wfn",
        "wholefilename",
        "nowfn",
        "nowholefilename",
        "exact",
        "wholeword",
        "ww",
        "nowholeword",
        "noww",
        "nowildcards",
    }
)
UNIVERSAL_WILDCARDS = frozenset({"*", "*.*"})
# Name filters Everything always answers from its index without a substring scan.
NAME_INDEX_FUNCTIONS = frozenset({"ext", "wfn", "wholefilename"})
# Property filters that are only cheap when that property is indexed; otherwise
# Everything reads the value from disk for every candidate. Keys are the
# indexed-property names an adapter reports to compose_query.
PROPERTY_INDEX_FUNCTIONS: dict[str, frozenset[str]] = {
    "size": frozenset({"size"}),
    "date_modified": frozenset({"dm", "datemodified"}),
}
TYPE_MODIFIERS = frozenset({"file", "files", "folder", "folders"})
# Everything 1.4 search functions, modifiers, and default filter macros. Everything 1.4
# matches any other "word:" prefix as literal text, which silently finds nothing.
KNOWN_SEARCH_FUNCTIONS = (
    frozenset(
        {
            "attrib", "attributes", "child", "childcount", "childfile", "childfolder",
            "da", "dateaccessed", "dc", "datecreated", "dm", "datemodified", "dr", "daterun",
            "depth", "parents", "dupe", "namepartdupe", "attribdupe", "dadupe", "dcdupe",
            "dmdupe", "sizedupe", "empty", "endwith", "startwith", "ext", "filelist",
            "filelistfilename", "frn", "fsi", "len", "parent", "infolder", "nosubfolders",
            "rc", "recentchange", "root", "runcount", "shell", "size", "type", "path",
            "audio", "zip", "doc", "exe", "pic", "video",
        }
    )
    | CONTENT_FUNCTIONS
    | NARROWING_FUNCTIONS
    | PREFIX_MODIFIERS
    | FORCED_DISK_MODIFIERS
    | REGEX_FUNCTIONS
    | WILDCARD_FUNCTIONS
    | TYPE_MODIFIERS
)
EXTENSION_WILDCARD_PATTERN =re.compile(r"\*\.[^*?.\s]+")
MAX_QUERY_TOKENS = 256
MAX_QUERY_BRANCHES = 128


class QuerySyntaxError(ValueError):
    """Raised internally when a query cannot be analysed safely."""


class _Token(NamedTuple):
    kind: str
    value: str = ""


class _TermNode(NamedTuple):
    value: str


class _AndNode(NamedTuple):
    children: tuple["_QueryNode", ...]


class _OrNode(NamedTuple):
    children: tuple["_QueryNode", ...]


class _NotNode(NamedTuple):
    child: "_QueryNode"


_QueryNode: TypeAlias = _TermNode | _AndNode | _OrNode | _NotNode


class _Literal(NamedTuple):
    value: str
    negated: bool


class _TermInfo(NamedTuple):
    valid: bool
    meaningful: bool
    narrowing_filter: bool
    path_signal: bool
    root_path: bool
    content_search: bool
    disk_search: bool
    extension_filter: bool
    regex_search: bool
    pattern_modifier: bool


def compose_query(query: str, scope: str | None = None, indexed_properties: frozenset[str] = frozenset()) -> str:
    text = query.strip()
    if not scope or not scope.strip():
        return text
    normalized_scope = normalize_scope(scope)
    if not is_absolute_scope(normalized_scope):
        raise QuerySyntaxError("scope must be an absolute Windows drive or UNC path")
    recursive_scope = normalized_scope if normalized_scope.endswith("\\") else f"{normalized_scope}\\"
    scope_term = quote_everything_phrase(recursive_scope)
    # Group the caller's entire expression so OR precedence cannot detach a
    # branch from the trusted scope term (including non-default Everything settings).
    # Everything evaluates AND operands left to right, and the scope is a
    # full-path substring match. Let an anchored, indexed filter run first when
    # the expression starts with one; otherwise the scope is the cheaper prefilter.
    if leads_with_indexed_filter(text, indexed_properties):
        return f"<{text}> {scope_term}"
    return f"{scope_term} <{text}>".strip()


def leads_with_indexed_filter(query: str, indexed_properties: frozenset[str] = frozenset()) -> bool:
    """Return whether the scope may follow the query without widening disk or substring work.

    The first AND operand must be an anchored filter on indexed data, and no
    term may read from disk: a content or from-disk term placed before the
    scope would read every matching file in the whole index.
    """
    try:
        tokens = _tokenize(query.strip())
        node = _QueryParser(tokens).parse()
    except QuerySyntaxError:
        return False
    if any(token.kind == "TERM" and _reads_disk(token.value) for token in tokens):
        return False
    cheap_functions = NAME_INDEX_FUNCTIONS.union(
        *(functions for name, functions in PROPERTY_INDEX_FUNCTIONS.items() if name in indexed_properties)
    )
    return _is_cheap_leading_node(node, cheap_functions)


def _reads_disk(raw: str) -> bool:
    return _term_contains_function(raw, CONTENT_FUNCTIONS | FORCED_DISK_MODIFIERS) or _analyse_term(raw).content_search


def _is_cheap_leading_node(node: "_QueryNode", cheap_functions: frozenset[str]) -> bool:
    if isinstance(node, _TermNode):
        return _is_cheap_term(node.value, cheap_functions)
    if isinstance(node, _NotNode):
        return _is_cheap_leading_node(node.child, cheap_functions)
    if isinstance(node, _AndNode):
        return _is_cheap_leading_node(node.children[0], cheap_functions)
    return all(_is_cheap_leading_node(child, cheap_functions) for child in node.children)


def _is_cheap_term(raw: str, cheap_functions: frozenset[str]) -> bool:
    text = raw.strip()
    while True:
        name, separator, value = text.partition(":")
        if not separator or not FUNCTION_CHAIN_IDENTIFIER_PATTERN.fullmatch(name):
            break
        function = _canonical_identifier(name)
        if function in TYPE_MODIFIERS:
            if not value.strip():
                return True
            text = value
            continue
        return function in cheap_functions and bool(value.strip())
    pattern = _unquote_phrase(text)
    if "\\" in pattern or "/" in pattern or not ("*" in pattern or "?" in pattern):
        # Plain words are unanchored substring scans; path patterns match full paths.
        return False
    # Anchored wildcards (lite*) and extension wildcards (*.py) are checked against
    # the indexed name; other leading wildcards (*lite*) scan like substrings.
    return pattern[0] not in "*?" or bool(EXTENSION_WILDCARD_PATTERN.fullmatch(pattern))


def unknown_search_functions(query: str) -> tuple[str, ...]:
    """Return "word:" prefixes that Everything 1.4 does not define (it searches them as text)."""
    try:
        tokens = _tokenize(query.strip())
    except QuerySyntaxError:
        return ()
    unknown: list[str] = []
    for token in tokens:
        if token.kind != "TERM":
            continue
        text = token.value
        while True:
            name, separator, value = text.partition(":")
            if not separator or not FUNCTION_CHAIN_IDENTIFIER_PATTERN.fullmatch(name):
                break
            if len(name) == 1 and value[:1] in ("\\", "/"):
                break  # a drive path such as C:\Work
            if _canonical_identifier(name) not in KNOWN_SEARCH_FUNCTIONS:
                if name not in unknown:
                    unknown.append(name)
                break
            text = value
    return tuple(unknown)


def unknown_function_note(query: str) -> str | None:
    """Explain an empty result that was caused by a search function Everything 1.4 lacks."""
    unknown = unknown_search_functions(query)
    if not unknown:
        return None
    names = ", ".join(f"{name}:" for name in unknown)
    return (
        f"No matches: {names} is not an Everything 1.4 search function, so it was searched as literal text. "
        "Use wfn:NAME for an exact file name, NAME* for a prefix, or plain text for name-contains; "
        "call everything_syntax_help for more."
    )


def quote_everything_phrase(value: str) -> str:
    escaped = value.replace('"', '""')
    return f'"{escaped}"'


def normalize_scope(scope: str) -> str:
    text = scope.strip().strip('"')
    if not text:
        return text
    normalized = ntpath.normpath(str(PureWindowsPath(text)))
    if DRIVE_ROOT_PATTERN.match(normalized):
        return normalized if normalized.endswith("\\") else f"{normalized}\\"
    return normalized.rstrip("\\/")


def is_absolute_scope(value: str | None) -> bool:
    if value is None or not value.strip():
        return False
    normalized = normalize_scope(value)
    path = PureWindowsPath(normalized)
    return path.is_absolute() and bool(path.drive) and bool(path.root)


def is_path_within_scope(path: str, scope: str | None) -> bool:
    if scope is None or not scope.strip():
        return True
    if not is_absolute_scope(scope):
        return False
    normalized_path = ntpath.normcase(ntpath.normpath(path.strip().strip('"')))
    normalized_scope = ntpath.normcase(normalize_scope(scope))
    if not PureWindowsPath(normalized_path).is_absolute():
        return False
    try:
        return ntpath.commonpath((normalized_path, normalized_scope)) == normalized_scope
    except ValueError:
        return False


def is_drive_root(value: str | None) -> bool:
    if value is None:
        return False
    return bool(DRIVE_ROOT_PATTERN.match(_unquote_path(value)))


def is_unc_root(value: str | None) -> bool:
    if value is None:
        return False
    return bool(UNC_ROOT_PATTERN.match(_unquote_path(value)))


def is_root_scope(value: str | None) -> bool:
    return is_drive_root(value) or is_unc_root(value)


def is_safe_query(query: str, scope: str | None = None) -> bool:
    """Return whether every possible Everything query branch is acceptably narrow."""
    text = query.strip()
    if not text or is_root_scope(scope) or bool(scope and scope.strip() and not is_absolute_scope(scope)):
        return False
    try:
        tree = _QueryParser(_tokenize(text)).parse()
        if not _or_alternatives_are_meaningful(tree):
            return False
        branches = _to_branches(tree)
    except (QuerySyntaxError, re.error):
        return False

    has_scope = bool(scope and scope.strip())
    for branch in branches:
        analysed = tuple((_analyse_term(literal.value), literal.negated) for literal in branch)
        if any(not info.valid for info, _ in analysed):
            return False
        positive = tuple(info for info, negated in analysed if not negated)
        if any(info.root_path for info in positive):
            return False
        meaningful = tuple(info for info in positive if info.meaningful)
        if not meaningful:
            return False
        if any(info.regex_search for info, _ in analysed) and not any(
            info.narrowing_filter
            and not info.content_search
            and not info.disk_search
            and not info.pattern_modifier
            for info in positive
        ):
            return False
        if any(info.content_search or info.disk_search for info, _ in analysed):
            if not has_scope or not any(
                info.narrowing_filter and not info.content_search and not info.disk_search for info in positive
            ):
                return False
        if has_scope:
            continue
        if not any(info.path_signal or info.narrowing_filter for info in meaningful):
            return False
        if meaningful and all(info.extension_filter for info in meaningful):
            return False
    return True


def has_root_path_expression(query: str) -> bool:
    for term in _query_terms(query):
        value = term[5:] if term.lower().startswith("path:") else term
        if is_root_scope(value):
            return True
    return False


def has_positive_narrowing_filter(query: str) -> bool:
    return any(POSITIVE_FILTER_PATTERN.match(term) for term in _positive_terms(query))


def has_exclusion(query: str) -> bool:
    return any(_is_exclusion_term(term) for term in _query_terms(query))


def is_exclusion_only_query(query: str) -> bool:
    terms = _query_terms(query)
    return bool(terms) and has_exclusion(query) and all(_is_exclusion_term(term) for term in terms)


def is_extension_only_query(query: str) -> bool:
    positive_terms = _positive_terms(query)
    return len(positive_terms) == 1 and positive_terms[0].lower().startswith("ext:")


def has_strong_filter(query: str) -> bool:
    """Compatibility name for positive narrowing filters only."""
    return has_positive_narrowing_filter(query)


def has_path_signal(query: str) -> bool:
    for term in _query_terms(query):
        if term.lower().startswith("path:"):
            return True
        value = _unquote_path(term)
        if re.match(r"(?:[a-z]:[\\/]|[\\/]{2})", value, re.IGNORECASE):
            return True
    return False


def _query_terms(query: str) -> tuple[str, ...]:
    """Return whitespace-separated terms while keeping quoted phrases intact."""
    terms: list[str] = []
    position = 0
    while position < len(query):
        while position < len(query) and query[position].isspace():
            position += 1
        if position == len(query):
            break

        start = position
        if query[position] == "!":
            position += 1
        if query[position : position + 5].lower() == "path:":
            position += 5

        if position < len(query) and query[position] == '"':
            position += 1
            while position < len(query):
                if query[position] != '"':
                    position += 1
                elif position + 1 < len(query) and query[position + 1] == '"':
                    position += 2
                else:
                    position += 1
                    break
        else:
            while position < len(query) and not query[position].isspace():
                position += 1

        terms.append(query[start:position])
    return tuple(terms)


def _is_exclusion_term(term: str) -> bool:
    return term.startswith("!")


def _positive_terms(query: str) -> tuple[str, ...]:
    return tuple(term for term in _query_terms(query) if not _is_exclusion_term(term))


def _unquote_path(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        return text[1:-1].replace('""', '"')
    return text


def _tokenize(query: str) -> tuple[_Token, ...]:
    tokens: list[_Token] = []
    position = 0
    expecting_factor = True
    while position < len(query):
        if query[position].isspace():
            if not expecting_factor:
                expecting_factor = True
            position += 1
            continue
        character = query[position]
        if character == "!" and expecting_factor:
            tokens.append(_Token("NOT"))
            position += 1
            continue
        if character in "|<>":
            tokens.append(_Token({"|": "OR", "<": "LT", ">": "GT"}[character]))
            expecting_factor = character in "|<"
            position += 1
            continue

        start = position
        quoted = False
        comparison_consumed = False
        while position < len(query):
            character = query[position]
            if character == '"':
                if quoted and position + 1 < len(query) and query[position + 1] == '"':
                    position += 2
                    continue
                quoted = not quoted
                position += 1
                continue
            if not quoted:
                if character.isspace() or character == "|":
                    break
                if character in "<>":
                    if not comparison_consumed and query[start:position].endswith(":"):
                        comparison_consumed = True
                        position += 1
                        continue
                    break
            position += 1
        if quoted:
            raise QuerySyntaxError("unterminated quote")
        value = query[start:position]
        if not value:
            raise QuerySyntaxError("empty term")
        tokens.append(_Token("TERM", value))
        expecting_factor = False
        if len(tokens) > MAX_QUERY_TOKENS:
            raise QuerySyntaxError("query is too complex")
    if not tokens:
        raise QuerySyntaxError("empty query")
    return tuple(tokens)


class _QueryParser:
    def __init__(self, tokens: tuple[_Token, ...]) -> None:
        self.tokens = tokens
        self.position = 0

    def parse(self) -> _QueryNode:
        node = self._parse_and()
        if self.position != len(self.tokens):
            raise QuerySyntaxError("unexpected token")
        return node

    def _parse_and(self) -> _QueryNode:
        children = [self._parse_or()]
        while self._peek_kind() in {"TERM", "NOT", "LT"}:
            children.append(self._parse_or())
        return children[0] if len(children) == 1 else _AndNode(tuple(children))

    def _parse_or(self) -> _QueryNode:
        children = [self._parse_factor()]
        while self._peek_kind() == "OR":
            self.position += 1
            if self._peek_kind() not in {"TERM", "NOT", "LT"}:
                raise QuerySyntaxError("empty OR alternative")
            children.append(self._parse_factor())
        return children[0] if len(children) == 1 else _OrNode(tuple(children))

    def _parse_factor(self) -> _QueryNode:
        negated = False
        while self._peek_kind() == "NOT":
            negated = not negated
            self.position += 1
        token = self._peek()
        if token is None:
            raise QuerySyntaxError("missing term")
        if token.kind == "TERM":
            self.position += 1
            node: _QueryNode = _TermNode(token.value)
        elif token.kind == "LT":
            self.position += 1
            if self._peek_kind() == "GT":
                raise QuerySyntaxError("empty group")
            node = self._parse_and()
            if self._peek_kind() != "GT":
                raise QuerySyntaxError("unclosed group")
            self.position += 1
        else:
            raise QuerySyntaxError("missing term")
        return _NotNode(node) if negated else node

    def _peek(self) -> _Token | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def _peek_kind(self) -> str | None:
        token = self._peek()
        return token.kind if token is not None else None


def _to_branches(node: _QueryNode, negated: bool = False) -> tuple[tuple[_Literal, ...], ...]:
    if isinstance(node, _TermNode):
        return ((_Literal(node.value, negated),),)
    if isinstance(node, _NotNode):
        return _to_branches(node.child, not negated)
    if isinstance(node, _AndNode):
        if negated:
            return _join_or(tuple(_to_branches(child, True) for child in node.children))
        return _join_and(tuple(_to_branches(child) for child in node.children))
    if negated:
        return _join_and(tuple(_to_branches(child, True) for child in node.children))
    return _join_or(tuple(_to_branches(child) for child in node.children))


def _join_or(groups: tuple[tuple[tuple[_Literal, ...], ...], ...]) -> tuple[tuple[_Literal, ...], ...]:
    result = tuple(branch for group in groups for branch in group)
    if len(result) > MAX_QUERY_BRANCHES:
        raise QuerySyntaxError("query has too many alternatives")
    return result


def _join_and(groups: tuple[tuple[tuple[_Literal, ...], ...], ...]) -> tuple[tuple[_Literal, ...], ...]:
    result: tuple[tuple[_Literal, ...], ...] = ((),)
    for group in groups:
        result = tuple(left + right for left in result for right in group)
        if len(result) > MAX_QUERY_BRANCHES:
            raise QuerySyntaxError("query has too many alternatives")
    return result


def _or_alternatives_are_meaningful(node: _QueryNode) -> bool:
    if isinstance(node, _TermNode):
        return _analyse_term(node.value).valid
    if isinstance(node, _NotNode):
        return _or_alternatives_are_meaningful(node.child)
    if isinstance(node, _AndNode):
        return all(_or_alternatives_are_meaningful(child) for child in node.children)
    for alternative in node.children:
        branches = _to_branches(alternative)
        if not all(
            any(not literal.negated and _analyse_term(literal.value).meaningful for literal in branch)
            for branch in branches
        ):
            return False
        if not _or_alternatives_are_meaningful(alternative):
            return False
    return True


def _analyse_term(raw: str) -> _TermInfo:
    text = raw.strip()
    content_search = _term_contains_function(text, CONTENT_FUNCTIONS)
    disk_search = _term_contains_function(text, FORCED_DISK_MODIFIERS)
    regex_search = _term_contains_function(text, REGEX_FUNCTIONS)
    wildcard_search = _term_contains_function(text, WILDCARD_FUNCTIONS)
    active_regex = False
    active_wildcards = False
    path_signal = False
    while True:
        if text.startswith("::"):
            text = text[2:]
        name, separator, value = text.partition(":")
        if not separator:
            break
        lowered_name = _canonical_identifier(name)
        if lowered_name == "path":
            path_signal = True
            text = value
            continue
        if lowered_name == "regex":
            active_regex = True
            text = value
            continue
        if lowered_name == "wildcards":
            active_wildcards = True
            text = value
            continue
        if lowered_name in FORCED_DISK_MODIFIERS:
            text = value
            continue
        if lowered_name in PREFIX_MODIFIERS:
            text = value
            continue
        break

    function_name, separator, function_value = text.partition(":")
    lowered_function = _canonical_identifier(function_name)
    content_search = content_search or bool(separator and lowered_function in CONTENT_FUNCTIONS)
    extension_filter = bool(separator and lowered_function == "ext")
    if active_regex or active_wildcards:
        nested_function = bool(separator and FUNCTION_CHAIN_IDENTIFIER_PATTERN.fullmatch(function_name))
        value = _unquote_phrase(function_value if nested_function else text)
        path_signal = path_signal or bool(
            nested_function and lowered_function in {"path", "parent", "infolder", "nosubfolders"}
        )
        meaningful, valid = _pattern_is_meaningful(value, active_regex)
        return _TermInfo(
            valid=valid,
            meaningful=meaningful,
            narrowing_filter=meaningful and not content_search and not regex_search,
            path_signal=path_signal,
            root_path=path_signal and is_root_scope(value),
            content_search=content_search,
            disk_search=disk_search,
            extension_filter=extension_filter,
            regex_search=regex_search,
            pattern_modifier=True,
        )
    if content_search or separator and lowered_function in NARROWING_FUNCTIONS:
        value = _unquote_phrase(function_value)
        regex_mode = regex_search
        path_signal = path_signal or lowered_function in {"path", "parent", "infolder", "nosubfolders"}
        meaningful, valid = _pattern_is_meaningful(value, regex_mode)
        narrowing_filter = meaningful and not content_search
        root_path = path_signal and is_root_scope(value)
        return _TermInfo(
            valid=valid,
            meaningful=meaningful,
            narrowing_filter=narrowing_filter,
            path_signal=path_signal,
            root_path=root_path,
            content_search=content_search,
            disk_search=disk_search,
            extension_filter=extension_filter,
            regex_search=regex_search,
            pattern_modifier=regex_search or wildcard_search,
        )

    value = _unquote_phrase(text)
    meaningful, valid = _pattern_is_meaningful(value, None)
    unquoted = _unquote_path(value)
    raw_path_signal = bool(re.match(r"(?:[a-z]:[\\/]|[\\/]{2})", unquoted, re.IGNORECASE))
    path_signal = path_signal or raw_path_signal
    return _TermInfo(
        valid=valid,
        meaningful=meaningful,
        narrowing_filter=meaningful and path_signal and not content_search and not disk_search,
        path_signal=path_signal,
        root_path=path_signal and is_root_scope(unquoted),
        content_search=content_search,
        disk_search=disk_search,
        extension_filter=False,
        regex_search=regex_search,
        pattern_modifier=regex_search or wildcard_search,
    )


def _canonical_identifier(value: str) -> str:
    return value.lower().lstrip("?").removesuffix("*").replace("-", "")


def _term_contains_function(raw: str, names: frozenset[str]) -> bool:
    text = raw.strip()
    while True:
        if text.startswith("::"):
            text = text[2:]
        name, separator, value = text.partition(":")
        if not separator or not FUNCTION_CHAIN_IDENTIFIER_PATTERN.fullmatch(name):
            return False
        if _canonical_identifier(name) in names:
            return True
        text = value


def _pattern_is_meaningful(value: str, regex_mode: bool | None) -> tuple[bool, bool]:
    stripped = value.strip()
    if not stripped or stripped.lower() in {"file:", "folder:"}:
        return False, True
    if regex_mode:
        try:
            re.compile(stripped)
        except re.error:
            return False, False
        return True, True
    if stripped in UNIVERSAL_WILDCARDS or stripped and set(stripped) == {"*"}:
        return False, True
    return True, True


def _unquote_phrase(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        return text[1:-1].replace('""', '"')
    return text
