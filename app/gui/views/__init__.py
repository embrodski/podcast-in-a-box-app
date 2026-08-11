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
from app.gui.views.done_screen import DoneScreen
from app.gui.views.new_session_screen import NewSessionScreen
from app.gui.views.placeholder_screen import PlaceholderScreen
from app.gui.views.processing_screen import ProcessingScreen
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
from app.gui.views.review_screens import (
    EstimateFullScreen,
    FullRenderScreen,
    OneMinReviewScreen,
    SyncOffsetReviewScreen,
    SyncOffsetReviewScreen,
)
from app.gui.views.welcome_screen import WelcomeScreen

__all__ = [
    "ApplyLabelsScreen",
    "CameraSetupScreen",
    "ConfirmSourceScreen",
    "CreateSessionScreen",
    "DeliveryScreen",
    "DoneScreen",
    "ErrorScreen",
    "EstimateFullScreen",
    "EstimatePrepScreen",
    "FullRenderScreen",
    "LabelCamerasScreen",
    "LabelMicrophonesScreen",
    "NewSessionScreen",
    "OneMinReviewScreen",
    "PlaceholderScreen",
    "PreflightScreen",
    "ProcessingScreen",
    "RecordingCompleteScreen",
    "RecordingSavedScreen",
    "RecordingScreen",
    "ResumeScreen",
    "SessionNameScreen",
    "SessionReadyScreen",
    "SourceLocationScreen",
    "VmixEnsureScreen",
    "VmixPresetScreen",
    "WelcomeScreen",
]
