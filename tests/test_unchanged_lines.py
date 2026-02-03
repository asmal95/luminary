"""Tests for unchanged lines functionality"""

from luminary.domain.models.comment import Comment
from luminary.domain.models.file_change import FileChange, Hunk


def test_file_change_get_line_type_for_unchanged_lines():
    """Test that get_line_type correctly identifies unchanged lines"""
    # Create a file change with hunks
    hunks = [
        Hunk(
            old_start=1,
            old_count=3,
            new_start=1,
            new_count=3,
            lines=[
                " unchanged line 1",  # line 1
                "+added line",  # line 2
                " unchanged line 2",  # line 3
            ],
        )
    ]

    file_change = FileChange(path="test.py", hunks=hunks)

    # Check line types
    assert file_change.get_line_type(1) == "unchanged"
    assert file_change.get_line_type(2) == "new"
    assert file_change.get_line_type(3) == "unchanged"


def test_file_change_get_line_type_for_deleted_lines():
    """Test that get_line_type correctly identifies deleted lines"""
    hunks = [
        Hunk(
            old_start=1,
            old_count=2,
            new_start=1,
            new_count=1,
            lines=[
                " unchanged line",
                "-deleted line",
            ],
        )
    ]

    file_change = FileChange(path="test.py", hunks=hunks)

    # Line 1 is unchanged in new file
    assert file_change.get_line_type(1) == "unchanged"


def test_file_change_get_line_type_outside_hunks():
    """Test that lines outside hunks are considered unchanged"""
    hunks = [
        Hunk(
            old_start=10,
            old_count=2,
            new_start=10,
            new_count=2,
            lines=[
                "+added line",
                " unchanged line",
            ],
        )
    ]

    file_change = FileChange(path="test.py", hunks=hunks)

    # Lines outside hunks should be unchanged
    assert file_change.get_line_type(1) == "unchanged"
    assert file_change.get_line_type(5) == "unchanged"
    assert file_change.get_line_type(100) == "unchanged"


def test_comment_has_line_type_field():
    """Test that Comment has line_type field with default value"""
    comment = Comment(content="Test comment", line_number=1)

    # Default line_type should be "new"
    assert comment.line_type == "new"


def test_comment_can_set_line_type():
    """Test that Comment can have custom line_type"""
    comment_unchanged = Comment(content="Test", line_number=1, line_type="unchanged")
    comment_old = Comment(content="Test", line_number=1, line_type="old")
    comment_new = Comment(content="Test", line_number=1, line_type="new")

    assert comment_unchanged.line_type == "unchanged"
    assert comment_old.line_type == "old"
    assert comment_new.line_type == "new"


def test_file_change_no_hunks_returns_unchanged():
    """Test that file without hunks returns unchanged for all lines"""
    file_change = FileChange(path="test.py", hunks=[])

    assert file_change.get_line_type(1) == "unchanged"
    assert file_change.get_line_type(100) == "unchanged"
