"""Typed CLI errors mapped to deterministic process exit codes.

Exit codes (see plan.md §3):
  0  success
  1  step error
  2  bad args / missing artifact
  3  eval assertion failed
  4  remote action blocked (no --allow-remote)
"""


class CliError(Exception):
    """Base class — carries the process exit code."""

    exit_code = 1


class StepFailed(CliError):
    """A step function raised or produced no usable result."""

    exit_code = 1


class ArtifactMissing(CliError):
    """A required upstream artifact is absent from the data dir."""

    exit_code = 2


class InvalidArtifact(CliError):
    """An artifact is present but fails a shape/sanity check (wrong file).

    Exit 2 like ArtifactMissing — both are "the input the step needs is not
    usable" — but distinct so callers can tell *missing* from *malformed*.
    """

    exit_code = 2


class EvalFailed(CliError):
    """An eval assertion threshold was breached."""

    exit_code = 3


class RemoteBlocked(CliError):
    """A Google/network side-effect was requested without --allow-remote."""

    exit_code = 4
