# pylint: disable=unused-variable, protected-access
"""TODO: add more tests for API"""
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep
from typing import Callable, Optional, Tuple
from unittest.mock import call, patch

import git
import pytest
from freezegun import freeze_time

import gto
import tests.resources
from gto.api import show
from gto.exceptions import RefNotFound, WrongArgs
from gto.index import RepoIndexManager
from gto.tag import find
from gto.versions import SemVer
from tests.skip_presets import skip_for_windows
from tests.utils import (
    check_obj,
    convert_objects_to_str_in_json_serializable_object,
)


def test_empty_index(empty_git_repo: Tuple[git.Repo, Callable]):
    repo, write_file = empty_git_repo
    with RepoIndexManager.from_repo(repo) as index:
        assert isinstance(index, RepoIndexManager)
        assert len(index.artifact_centric_representation()) == 0


def test_empty_state(empty_git_repo: Tuple[git.Repo, Callable]):
    repo, write_file = empty_git_repo
    state = gto.api._get_state(repo.working_dir)
    assert len(state.artifacts) == 0


def test_api_info_commands_empty_repo(empty_git_repo: Tuple[git.Repo, Callable]):
    repo, write_file = empty_git_repo
    gto.api.show(repo.working_dir)
    gto.api.history(repo.working_dir)


@pytest.fixture
def repo_with_artifact(init_showcase_semver):
    repo: git.Repo
    repo, write_file = init_showcase_semver
    with open(
        os.path.join(repo.working_dir, "artifacts.yaml"), "w", encoding="utf8"
    ) as f:
        f.write("rf: \n  type: model\n  path: models/random-forest.pkl\n")
    repo.index.add(["artifacts.yaml"])
    repo.index.commit("Commit 1")
    with open(
        os.path.join(repo.working_dir, "artifacts.yaml"), "w", encoding="utf8"
    ) as f:
        f.write("rf: \n  type: model\n  path: models/random-forest.pklx\n")
    repo.index.add(["artifacts.yaml"])
    repo.index.commit("Commit 2")
    return repo, "new-artifact"


# def test_api_info_commands_repo_with_artifact(
#     repo_with_artifact: Tuple[git.Repo, Callable]
# ):
#     repo, write_file = repo_with_artifact
#     gto.api.show(repo)
#     gto.api.show(repo, "new-artifact")
#     gto.api.history(repo)


# def test_describe(repo_with_artifact: Tuple[git.Repo, Callable]):
#     repo, write_file = repo_with_artifact
#     gto.api.annotate(repo, "new-artifact", path="other-path")
#     check_obj(
#         gto.api.describe(repo, "new-artifact").dict(exclude_defaults=True),  # type: ignore
#         dict(
#             type="new-type",
#             path="other-path",
#         ),
#     )
#     check_obj(
#         gto.api.describe(repo, "new-artifact", rev="HEAD").dict(exclude_defaults=True),  # type: ignore
#         dict(
#             type="new-type",
#             path="path",
#         ),
#     )


def test_register_deregister(repo_with_artifact):
    repo, name = repo_with_artifact
    vname1, vname2 = "v1.0.0", "v1.0.1"
    gto.api.register(repo.working_dir, name, "HEAD", vname1)
    latest = gto.api.find_latest_version(repo.working_dir, name)
    assert latest.version == vname1
    with open("tmp.txt", "w", encoding="utf8") as f:
        f.write("some text")
    repo.index.commit(
        "Irrelevant action to create a git commit to register another version"
    )
    message = "Some message"
    author = "GTO"
    author_email = "gto@iterative.ai"
    gto.api.register(
        repo.working_dir,
        name,
        "HEAD",
        message=message,
        author=author,
        author_email=author_email,
    )
    latest = gto.api.find_latest_version(repo.working_dir, name)
    assert latest.version == vname2
    assert latest.message == message
    assert latest.author == author
    assert latest.author_email == author_email

    assert len(gto.api.show(repo.working_dir, name, deprecated=False)) == 2

    # test _show_versions
    assert (
        gto.api._show_versions(repo.working_dir, name, ref="HEAD")[0]["ref"]
        == f"{name}@v1.0.1"
    )
    assert (
        gto.api._show_versions(repo.working_dir, name, ref="HEAD^1")[0]["ref"]
        == f"{name}@v1.0.0"
    )
    assert (
        gto.api._show_versions(repo.working_dir, name, ref=repo.commit().hexsha)[0][
            "ref"
        ]
        == f"{name}@v1.0.1"
    )
    with pytest.raises(RefNotFound):
        gto.api._show_versions(repo.working_dir, name, ref="HEAD^2")

    gto.api.deregister(repo=repo.working_dir, name=name, version=vname2)
    latest = gto.api.find_latest_version(repo.working_dir, name)
    assert latest.version == vname1

    assert len(gto.api.show(repo.working_dir, name, deprecated=False)) == 1
    assert len(gto.api.show(repo.working_dir, name, deprecated=True)) == 2
    assert len(gto.api._show_versions(repo.working_dir, name, ref="HEAD")) == 0


@pytest.mark.parametrize(
    "name",
    (
        "model",
        "folder:artifact",
        "some/folder:some/artifact",
    ),
)
def test_assign(repo_with_artifact: Tuple[git.Repo, str], name):
    repo, _ = repo_with_artifact
    stage = "staging"
    repo.create_tag("v1.0.0")
    repo.create_tag("wrong-tag-unrelated")
    message = "some msg"
    author = "GTO"
    author_email = "gto@iterative.ai"
    event = gto.api.assign(
        repo.working_dir,
        name,
        stage,
        ref="HEAD",
        name_version="v0.0.1",
        message=message,
        author=author,
        author_email=author_email,
    )
    assignments = gto.api.find_versions_in_stage(repo.working_dir, name, stage)
    assert len(assignments) == 1
    check_obj(
        assignments[0].dict_state(),
        dict(
            artifact=name,
            version="v0.0.1",
            stage=stage,
            author=author,
            author_email=author_email,
            message=message,
            commit_hexsha=repo.commit().hexsha,
            is_active=True,
            ref=event.ref,
        ),
        {"created_at", "assignments", "unassignments", "tag", "activated_at"},
    )
    event = gto.api.assign(
        repo.working_dir,
        name,
        stage,
        ref="HEAD^1",
        name_version="v0.0.2",
        message=message,
        author=author,
        author_email=author_email,
    )
    assignments = gto.api.find_versions_in_stage(repo.working_dir, name, stage)
    assert len(assignments) == 1
    assignments = gto.api.find_versions_in_stage(
        repo.working_dir, name, stage, versions_per_stage=-1
    )
    assert len(assignments) == 2


def test_assign_skip_registration(repo_with_artifact: Tuple[git.Repo, str]):
    repo, name = repo_with_artifact
    stage = "staging"
    with pytest.raises(WrongArgs):
        gto.api.assign(
            repo.working_dir,
            name,
            stage,
            ref="HEAD",
            name_version="v0.0.1",
            skip_registration=True,
        )
    gto.api.assign(repo.working_dir, name, stage, ref="HEAD", skip_registration=True)
    assignments = gto.api.find_versions_in_stage(repo.working_dir, name, stage)
    assert len(assignments) == 1
    assert not SemVer.is_valid(assignments[0].version)


def test_assign_force_is_needed(repo_with_artifact: Tuple[git.Repo, str]):
    repo, name = repo_with_artifact
    gto.api.assign(repo, name, "staging", ref="HEAD")
    gto.api.assign(repo, name, "staging", ref="HEAD^1")
    with pytest.raises(WrongArgs):
        gto.api.assign(repo, name, "staging", ref="HEAD")
    with pytest.raises(WrongArgs):
        gto.api.assign(repo, name, "staging", ref="HEAD^1")
    gto.api.assign(repo, name, "staging", ref="HEAD", force=True)
    gto.api.assign(repo, name, "staging", ref="HEAD^1", force=True)


def test_unassign(repo_with_artifact):
    repo, _ = repo_with_artifact
    gto.api.register(repo.working_dir, name="model", ref="HEAD")
    gto.api.assign(repo.working_dir, name="model", ref="HEAD", stage="dev")
    assert (
        gto.api.find_versions_in_stage(repo.working_dir, name="model", stage="dev")
        is not None
    )

    gto.api.unassign(repo.working_dir, name="model", ref="HEAD", stage="dev")
    assert (
        gto.api.find_versions_in_stage(repo.working_dir, name="model", stage="dev")
        is None
    )


def test_deprecate(repo_with_artifact):
    repo, _ = repo_with_artifact
    gto.api.register(repo.working_dir, name="model", ref="HEAD")
    assert len(gto.api.show(repo.working_dir, "model")) == 1

    sleep(1)
    gto.api.deprecate(repo.working_dir, name="model")
    assert len(gto.api.show(repo.working_dir, "model", deprecated=False)) == 0
    assert len(gto.api.show(repo.working_dir, "model", deprecated=True)) == 1

    with pytest.raises(WrongArgs):
        gto.api.deprecate(repo.working_dir, name="model")
        gto.api.deprecate(repo.working_dir, name="model", simple=True, force=True)
    gto.api.deprecate(repo.working_dir, name="model", force=True)


@contextmanager
def environ(**overrides):
    old = {name: os.environ[name] for name in overrides if name in os.environ}
    to_del = set(overrides) - set(old)
    try:
        os.environ.update(overrides)
        yield
    finally:
        os.environ.update(old)
        for name in to_del:
            os.environ.pop(name, None)


def test_check_ref_detailed(repo_with_artifact: Tuple[git.Repo, Callable]):
    repo, name = repo_with_artifact

    NAME = "model"
    SEMVER = "v1.2.3"
    GIT_AUTHOR_NAME = "Alexander Guschin"
    GIT_AUTHOR_EMAIL = "aguschin@iterative.ai"
    GIT_COMMITTER_NAME = "Oliwav"
    GIT_COMMITTER_EMAIL = "oliwav@iterative.ai"

    with environ(
        GIT_AUTHOR_NAME=GIT_AUTHOR_NAME,
        GIT_AUTHOR_EMAIL=GIT_AUTHOR_EMAIL,
        GIT_COMMITTER_NAME=GIT_COMMITTER_NAME,
        GIT_COMMITTER_EMAIL=GIT_COMMITTER_EMAIL,
    ):
        gto.api.register(repo, name=NAME, ref="HEAD", version=SEMVER)

    events = gto.api.check_ref(repo, f"{NAME}@{SEMVER}")
    assert len(events) == 1, "Should return one event"
    check_obj(
        events[0].dict_state(),
        {
            "event": "registration",
            "artifact": NAME,
            "version": SEMVER,
            "author": GIT_COMMITTER_NAME,
            "author_email": GIT_COMMITTER_EMAIL,
            "tag": f"{NAME}@{SEMVER}",
        },
        skip_keys={"commit_hexsha", "created_at", "message", "priority", "addition"},
    )


def test_check_ref_multiple_showcase(showcase):
    repo: git.Repo
    (
        path,
        repo,
        write_file,
        first_commit,
        second_commit,
    ) = showcase

    for tag in find(repo=repo):
        events = gto.api.check_ref(repo, tag.name)
        assert len(events) == 1, "Should return one event"
        assert events[0].ref == tag.name


def test_check_ref_catch_the_bug(repo_with_artifact: Tuple[git.Repo, Callable]):
    repo, name = repo_with_artifact
    NAME = "artifact"
    gto.api.register(repo, NAME, "HEAD")
    assignment1 = gto.api.assign(repo, NAME, "staging", ref="HEAD")
    assignment2 = gto.api.assign(repo, NAME, "prod", ref="HEAD")
    assignment3 = gto.api.assign(repo, NAME, "dev", ref="HEAD")
    for assignment, tag in zip(
        [assignment1, assignment2, assignment3],
        [f"{NAME}#staging#1", f"{NAME}#prod#2", f"{NAME}#dev#3"],
    ):
        events = gto.api.check_ref(repo, tag)
        assert len(events) == 1, events
        assert events[0].ref == assignment.tag == tag


def test_is_not_gto_repo(empty_git_repo):
    repo, _ = empty_git_repo
    assert not gto.api._is_gto_repo(repo.working_dir)


def test_is_gto_repo_because_of_config(init_showcase_semver):
    repo, _ = init_showcase_semver
    assert gto.api._is_gto_repo(repo.working_dir)


def test_is_gto_repo_because_of_registered_artifact(repo_with_commit):
    repo, _ = repo_with_commit
    gto.api.register(repo, "model", "HEAD", "v1.0.0")
    assert gto.api._is_gto_repo(repo)


def test_is_gto_repo_because_of_artifacts_yaml(empty_git_repo):
    repo, write_file = empty_git_repo
    write_file("artifacts.yaml", "{}")
    assert gto.api._is_gto_repo(repo)


@skip_for_windows
def test_if_show_on_remote_git_repo_then_return_expected_registry():
    result = show(repo=tests.resources.SAMPLE_REMOTE_REPO_URL)
    assert result == tests.resources.get_sample_remote_repo_expected_registry()


@skip_for_windows
@pytest.mark.parametrize(
    "ref,expected_stage,expected_version,expected_artifact",
    (
        ("churn#prod#2", "prod", "v3.0.0", "churn"),
        ("segment@v0.4.1", None, "v0.4.1", "segment"),
    ),
)
def test_if_check_ref_on_remote_git_repo_then_return_expected_reference(
    ref: str,
    expected_stage: Optional[str],
    expected_version: str,
    expected_artifact: str,
):
    result = gto.api.check_ref(repo=tests.resources.SAMPLE_REMOTE_REPO_URL, ref=ref)
    assert len(result) == 1
    if expected_stage is not None:
        assert result[0].stage == expected_stage
    else:
        assert hasattr(result[0], "stage") is False
    assert result[0].version == expected_version
    assert result[0].artifact == expected_artifact


@freeze_time("1996-06-09 00:00:00", tz_offset=0)
@skip_for_windows
def test_if_history_on_remote_git_repo_then_return_expected_history():
    result = gto.api.history(
        repo=tests.resources.SAMPLE_REMOTE_REPO_URL, artifact="churn"
    )
    assert (
        convert_objects_to_str_in_json_serializable_object(result)
        == tests.resources.get_sample_remote_repo_expected_history_churn()
    )


@skip_for_windows
def test_if_stages_on_remote_git_repo_then_return_expected_stages():
    result = gto.api.get_stages(repo=tests.resources.SAMPLE_REMOTE_REPO_URL)
    assert result == ["dev", "prod", "staging"]


@skip_for_windows
def test_if_describe_on_remote_git_repo_then_return_expected_info():
    result = gto.api.describe(repo=tests.resources.SAMPLE_REMOTE_REPO_URL, name="churn")
    assert result.dict(exclude_defaults=True) == {
        "type": "model",
        "path": "models/churn.pkl",
        "virtual": False,
    }


def test_if_register_with_auto_push_then_invoke_git_push_tag(repo_with_artifact):
    repo, _ = repo_with_artifact
    with patch("gto.registry.git_push_tag") as mocked_git_push_tags:
        gto.api.register(repo=repo.working_dir, name="model", ref="HEAD", push=True)
    mocked_git_push_tags.assert_called_once_with(
        repo=Path(repo.working_dir).as_posix(),
        tag_name="model@v0.0.1",
        delete=False,
    )


def test_if_assign_with_auto_push_then_invoke_git_push_tag_2_times_for_registration_and_promotion(
    repo_with_artifact,
):
    repo, _ = repo_with_artifact
    with patch("gto.registry.git_push_tag") as mocked_git_push_tags:
        gto.api.assign(
            repo.working_dir, name="model", stage="dev", ref="HEAD", push=True
        )
    expected_calls = [
        call(
            repo=Path(repo.working_dir).as_posix(),
            tag_name="model@v0.0.1",
            delete=False,
        ),
        call(
            repo=Path(repo.working_dir).as_posix(),
            tag_name="model#dev#1",
            delete=False,
        ),
    ]
    mocked_git_push_tags.assert_has_calls(expected_calls)


def test_if_unassign_with_auto_push_then_invoke_git_push_tag(repo_with_artifact):
    repo, _ = repo_with_artifact
    gto.api.assign(repo.working_dir, name="model", stage="dev", ref="HEAD", push=False)
    with patch("gto.registry.git_push_tag") as mocked_git_push_tags:
        gto.api.unassign(
            repo.working_dir,
            name="model",
            stage="dev",
            version="v0.0.1",
            push=True,
        )
    mocked_git_push_tags.assert_called_once_with(
        repo=Path(repo.working_dir).as_posix(),
        tag_name="model#dev!#2",
        delete=False,
    )


def test_if_unassign_with_delete_and_auto_push_then_invoke_git_push_tag(
    repo_with_artifact,
):
    repo, _ = repo_with_artifact
    gto.api.assign(repo.working_dir, name="model", stage="dev", ref="HEAD", push=False)
    with patch("gto.registry.git_push_tag") as mocked_git_push_tags:
        gto.api.unassign(
            repo.working_dir,
            name="model",
            stage="dev",
            version="v0.0.1",
            delete=True,
            push=True,
        )
    mocked_git_push_tags.assert_called_once_with(
        repo=Path(repo.working_dir).as_posix(), tag_name="model#dev#1", delete=True
    )


def test_if_deregister_with_auto_push_then_invoke_git_push_tag(repo_with_artifact):
    repo, _ = repo_with_artifact
    gto.api.register(repo.working_dir, name="model", ref="HEAD", push=False)
    with patch("gto.registry.git_push_tag") as mocked_git_push_tags:
        gto.api.deregister(repo.working_dir, name="model", version="v0.0.1", push=True)
    mocked_git_push_tags.assert_called_once_with(
        repo=Path(repo.working_dir).as_posix(),
        tag_name="model@v0.0.1!",
        delete=False,
    )


def test_if_deregister_with_delete_and_auto_push_then_invoke_git_push_tag(
    repo_with_artifact,
):
    repo, _ = repo_with_artifact
    gto.api.register(repo.working_dir, name="model", ref="HEAD", push=False)
    with patch("gto.registry.git_push_tag") as mocked_git_push_tags:
        gto.api.deregister(
            repo.working_dir,
            name="model",
            version="v0.0.1",
            push=True,
            delete=True,
        )
    mocked_git_push_tags.assert_called_once_with(
        repo=Path(repo.working_dir).as_posix(),
        tag_name="model@v0.0.1",
        delete=True,
    )


def test_if_deprecate_with_auto_push_then_invoke_git_push_tag(repo_with_artifact):
    repo, _ = repo_with_artifact
    gto.api.register(repo.working_dir, name="model", ref="HEAD", push=False)
    with patch("gto.registry.git_push_tag") as mocked_git_push_tags:
        gto.api.deprecate(repo.working_dir, name="model", push=True)
    mocked_git_push_tags.assert_called_once_with(
        repo=Path(repo.working_dir).as_posix(),
        tag_name="model@deprecated",
        delete=False,
    )


def test_if_deprecate_with_delete_and_auto_push_then_invoke_git_push_tag(
    repo_with_artifact,
):
    repo, _ = repo_with_artifact
    gto.api.register(repo.working_dir, name="model", ref="HEAD", push=False)
    with patch("gto.registry.git_push_tag") as mocked_git_push_tags:
        gto.api.deprecate(repo.working_dir, name="model", push=True, delete=True)
    mocked_git_push_tags.assert_called_once_with(
        repo=Path(repo.working_dir).as_posix(),
        tag_name="model@v0.0.1",
        delete=True,
    )


@skip_for_windows
def test_if_register_with_remote_repo_then_invoke_git_push_tag():
    with patch("gto.registry.git_push_tag") as mocked_git_push_tag:
        with patch("gto.git_utils.TemporaryDirectory") as MockedTemporaryDirectory:
            # pylint: disable=consider-using-with
            tmp_dir = TemporaryDirectory()
            MockedTemporaryDirectory.return_value = tmp_dir
            gto.api.register(
                repo=tests.resources.SAMPLE_REMOTE_REPO_URL,
                name="model",
                ref="HEAD",
            )
            mocked_git_push_tag.assert_called_once_with(
                repo=Path(tmp_dir.name).as_posix(),
                tag_name="model@v0.0.1",
                delete=False,
            )
            tmp_dir.cleanup()


@skip_for_windows
def test_if_assign_with_remote_repo_then_invoke_git_push_tag():
    with patch("gto.registry.git_push_tag") as mocked_git_push_tag:
        with patch("gto.git_utils.TemporaryDirectory") as MockedTemporaryDirectory:
            # pylint: disable=consider-using-with
            tmp_dir = TemporaryDirectory()
            MockedTemporaryDirectory.return_value = tmp_dir
            gto.api.assign(
                repo=tests.resources.SAMPLE_REMOTE_REPO_URL,
                name="model",
                stage="dev",
                ref="HEAD",
            )
            expected_calls = [
                call(
                    repo=Path(tmp_dir.name).as_posix(),
                    tag_name="model@v0.0.1",
                    delete=False,
                ),
                call(
                    repo=Path(tmp_dir.name).as_posix(),
                    tag_name="model#dev#1",
                    delete=False,
                ),
            ]
            mocked_git_push_tag.assert_has_calls(expected_calls)
            tmp_dir.cleanup()


@skip_for_windows
def test_if_deprecate_with_remote_repo_then_invoke_git_push_tag():
    with patch("gto.registry.git_push_tag") as mocked_git_push_tag:
        with patch("gto.git_utils.TemporaryDirectory") as MockedTemporaryDirectory:
            # pylint: disable=consider-using-with
            tmp_dir = TemporaryDirectory()
            MockedTemporaryDirectory.return_value = tmp_dir
            gto.api.deprecate(
                repo=tests.resources.SAMPLE_REMOTE_REPO_URL,
                name="churn",
            )
            mocked_git_push_tag.assert_called_once_with(
                repo=Path(tmp_dir.name).as_posix(),
                tag_name="churn@deprecated",
                delete=False,
            )
            tmp_dir.cleanup()


@skip_for_windows
def test_if_deregister_with_remote_repo_then_invoke_git_push_tag():
    with patch("gto.registry.git_push_tag") as mocked_git_push_tag:
        with patch("gto.git_utils.TemporaryDirectory") as MockedTemporaryDirectory:
            # pylint: disable=consider-using-with
            tmp_dir = TemporaryDirectory()
            MockedTemporaryDirectory.return_value = tmp_dir
            gto.api.deregister(
                repo=tests.resources.SAMPLE_REMOTE_REPO_URL,
                name="churn",
                version="v3.0.0",
            )
            mocked_git_push_tag.assert_called_once_with(
                repo=Path(tmp_dir.name).as_posix(),
                tag_name="churn@v3.0.0!",
                delete=False,
            )
            tmp_dir.cleanup()


@skip_for_windows
def test_if_unassign_with_remote_repo_then_invoke_git_push_tag():
    with patch("gto.registry.git_push_tag") as mocked_git_push_tag:
        with patch("gto.git_utils.TemporaryDirectory") as MockedTemporaryDirectory:
            # pylint: disable=consider-using-with
            tmp_dir = TemporaryDirectory()
            MockedTemporaryDirectory.return_value = tmp_dir
            gto.api.unassign(
                repo=tests.resources.SAMPLE_REMOTE_REPO_URL,
                name="churn",
                stage="staging",
                version="v3.1.0",
            )
            mocked_git_push_tag.assert_called_once_with(
                repo=Path(tmp_dir.name).as_posix(),
                tag_name="churn#staging!#3",
                delete=False,
            )
            tmp_dir.cleanup()
