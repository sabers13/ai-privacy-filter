"""Dataset helpers used for evaluation.

Only GermEval 2014 exists so far. Its loader downloads the data into
``data/external/`` (git-ignored) and parses it without ever printing sentence
text.
"""

from ai_privacy_filter.datasets.germeval import (
    GERMEVAL_CLASSES,
    PRIMARY_LABELS,
    REPORTED_SEPARATELY_LABELS,
    SOURCE_BY_SPLIT,
    SPLIT_SOURCES,
    GermEvalClass,
    GermEvalError,
    GermEvalFormatError,
    GermEvalIntegrityError,
    GermEvalSentence,
    GermEvalSource,
    GermEvalSpan,
    GermEvalSplitSizeError,
    SplitStats,
    check_published_sizes,
    parse_file,
    parse_tag,
    parse_tsv,
    select_sources,
    summarise,
)

__all__ = [
    "GERMEVAL_CLASSES",
    "PRIMARY_LABELS",
    "REPORTED_SEPARATELY_LABELS",
    "SOURCE_BY_SPLIT",
    "SPLIT_SOURCES",
    "GermEvalClass",
    "GermEvalError",
    "GermEvalFormatError",
    "GermEvalIntegrityError",
    "GermEvalSentence",
    "GermEvalSource",
    "GermEvalSpan",
    "GermEvalSplitSizeError",
    "SplitStats",
    "check_published_sizes",
    "parse_file",
    "parse_tag",
    "parse_tsv",
    "select_sources",
    "summarise",
]
