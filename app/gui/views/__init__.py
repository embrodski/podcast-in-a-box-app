"""GUI views."""

from app.gui.views.autocut_screens import (
    ConfirmSourceScreen,
    CreateSessionScreen,
    DeliveryScreen,
    SessionNameScreen,
    SessionReadyScreen,
    SourceLocationScreen,
)
from app.gui.views.labeling_screens import (
    ApplyLabelsScreen,
    EstimatePrepScreen,
    LabelCamerasScreen,
    LabelMicrophonesScreen,
)
from app.gui.views.error_screen import ErrorScreen
from app.gui.views.clean_working_screen import CleanWorkingFilesScreen
from app.gui.views.done_screen import DoneScreen
from app.gui.views.new_session_screen import NewSessionScreen
from app.gui.views.processing_screen import ProcessingScreen, e1_close_requires_confirm
from app.gui.views.preflight_screen import PreflightScreen
from app.gui.views.resume_screen import ResumeScreen
from app.gui.views.recording_screens import (
    CameraSetupScreen,
    RecordingCompleteScreen,
    RecordingSavedScreen,
    RecordingScreen,
    VmixEnsureScreen,
    VmixPresetScreen,
)
from app.gui.views.full_render_screen import FullRenderScreen
from app.gui.views.one_min_review_screen import OneMinReviewScreen
from app.gui.views.sync_offset_review_screen import SyncOffsetReviewScreen
from app.gui.views.welcome_screen import WelcomeScreen

__all__ = [
    "ApplyLabelsScreen",
    "CameraSetupScreen",
    "CleanWorkingFilesScreen",
    "ConfirmSourceScreen",
    "CreateSessionScreen",
    "DeliveryScreen",
    "DoneScreen",
    "ErrorScreen",
    "EstimatePrepScreen",
    "FullRenderScreen",
    "LabelCamerasScreen",
    "LabelMicrophonesScreen",
    "NewSessionScreen",
    "OneMinReviewScreen",
    "PreflightScreen",
    "ProcessingScreen",
    "e1_close_requires_confirm",
    "RecordingCompleteScreen",
    "RecordingSavedScreen",
    "RecordingScreen",
    "ResumeScreen",
    "SessionNameScreen",
    "SessionReadyScreen",
    "SourceLocationScreen",
    "SyncOffsetReviewScreen",
    "VmixEnsureScreen",
    "VmixPresetScreen",
    "WelcomeScreen",
]
