"""Identity resolution and person lifecycle use cases."""

from people_context.app.people.aliases import (
    AddAlias,
    AddAliasInput,
    AliasConflictError,
    AliasNotFoundError,
    RemoveAlias,
    RemoveAliasInput,
    RemoveAliasResult,
    UpdateAlias,
    UpdateAliasInput,
    UpdateAliasResult,
)
from people_context.app.people.edit import EditPerson, EditPersonInput, PersonNameCollisionError
from people_context.app.people.forget import Forget, ForgetError, ForgetPreview, ForgetResult, PreviewForget
from people_context.app.people.merge import MergeMovedCounts, MergePeople, MergePeopleError, MergePeopleResult
from people_context.app.people.remember import (
    AliasInput,
    AmbiguousPersonError,
    EmailNameError,
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
    "AliasConflictError",
    "AliasInput",
    "AliasNotFoundError",
    "AmbiguousPersonError",
    "base_match_reason",
    "EditPerson",
    "EditPersonInput",
    "EmailNameError",
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
    "RemoveAlias",
    "RemoveAliasInput",
    "RemoveAliasResult",
    "RememberPerson",
    "RememberPersonInput",
    "RememberPersonResult",
    "ResolutionCandidate",
    "ResolutionHints",
    "ResolutionResult",
    "ResolvePerson",
    "SearchPeople",
    "SelfAlreadyExistsError",
    "UpdateAlias",
    "UpdateAliasInput",
    "UpdateAliasResult",
]
