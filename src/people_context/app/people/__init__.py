"""Identity resolution and person lifecycle use cases."""

from people_context.app.people.aliases import AddAlias, AddAliasInput
from people_context.app.people.edit import EditPerson, EditPersonInput, PersonNameCollisionError
from people_context.app.people.forget import Forget, ForgetError, ForgetPreview, ForgetResult, PreviewForget
from people_context.app.people.merge import MergeMovedCounts, MergePeople, MergePeopleError, MergePeopleResult
from people_context.app.people.remember import (
    AliasInput,
    AmbiguousPersonError,
    RememberPerson,
    RememberPersonInput,
    RememberPersonResult,
    SelfAlreadyExistsError,
)
from people_context.app.people.resolve import (
    EXACT_MATCH_REASON,
    FUZZY_MATCH_REASON,
    ResolutionCandidate,
    ResolutionHints,
    ResolutionResult,
    ResolvePerson,
    base_match_reason,
)
from people_context.app.people.search import SearchPeople

__all__ = [
    "AddAlias",
    "AddAliasInput",
    "AliasInput",
    "AmbiguousPersonError",
    "base_match_reason",
    "EditPerson",
    "EditPersonInput",
    "EXACT_MATCH_REASON",
    "Forget",
    "ForgetError",
    "ForgetPreview",
    "ForgetResult",
    "FUZZY_MATCH_REASON",
    "MergeMovedCounts",
    "MergePeople",
    "MergePeopleError",
    "MergePeopleResult",
    "PersonNameCollisionError",
    "PreviewForget",
    "RememberPerson",
    "RememberPersonInput",
    "RememberPersonResult",
    "ResolutionCandidate",
    "ResolutionHints",
    "ResolutionResult",
    "ResolvePerson",
    "SearchPeople",
    "SelfAlreadyExistsError",
]
