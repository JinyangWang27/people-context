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
    FUZZY_MATCH_REASON,
    ResolutionCandidate,
    ResolutionHints,
    ResolutionResult,
    ResolvePerson,
)
from people_context.app.people.search import SearchPeople

__all__ = [
    "AddAlias",
    "AddAliasInput",
    "AliasInput",
    "AmbiguousPersonError",
    "EditPerson",
    "EditPersonInput",
    "Forget",
    "ForgetError",
    "ForgetPreview",
    "ForgetResult",
    "MergeMovedCounts",
    "MergePeople",
    "MergePeopleError",
    "MergePeopleResult",
    "PersonNameCollisionError",
    "PreviewForget",
    "RememberPerson",
    "RememberPersonInput",
    "RememberPersonResult",
    "FUZZY_MATCH_REASON",
    "ResolutionCandidate",
    "ResolutionHints",
    "ResolutionResult",
    "ResolvePerson",
    "SearchPeople",
    "SelfAlreadyExistsError",
]
