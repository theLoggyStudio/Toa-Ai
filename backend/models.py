from typing import Literal, Optional

from pydantic import BaseModel, Field

from languages import SourceLanguage, TargetLanguage

TaskStatus = Literal[
    "pending_payment", "paid", "processing", "completed", "failed"
]
TaskKind = Literal["translate", "restore"]
RestoreOption = Literal["tears", "color", "hd"]


class BoundingBox(BaseModel):
    x_min: int
    y_min: int
    x_max: int
    y_max: int


class TextBlock(BaseModel):
    id: int
    boundingBox: BoundingBox
    originalText: str
    translatedText: str = ""
    bubbleHtml: str = ""


class TranslationTask(BaseModel):
    id: str
    originalImagesCount: int
    sourceLanguage: SourceLanguage
    targetLanguage: TargetLanguage
    status: TaskStatus
    amountCFA: int
    billableBubblesCount: int = 0
    includeToa: bool = True
    kind: TaskKind = "translate"
    imageWidth: Optional[int] = None
    imageHeight: Optional[int] = None
    restoreOptions: list[RestoreOption] = Field(default_factory=list)
    restoredImageUrl: Optional[str] = None
    # exclude=True : jamais sérialisé vers le client (sert à vérifier le webhook).
    payduniaToken: Optional[str] = Field(default=None, exclude=True)
    pdfUrl: Optional[str] = None
    partialPdfUrl: Optional[str] = None
    progressPercent: int = 0
    progressMessage: Optional[str] = None
    errorMessage: Optional[str] = None


class AppConfigResponse(BaseModel):
    paymentDisabled: bool
    priceBaseCFA: int
    pricePerBubbleCFA: int
    eclatPriceMinCFA: int = 250
    eclatPriceMaxCFA: int = 750
    frescoOptionPriceCFA: int = 250


class UploadResponse(BaseModel):
    task: TranslationTask
    checkoutReady: bool = True
    paymentDisabled: bool = False


class StartProcessingResponse(BaseModel):
    task: TranslationTask
    message: str = "Traitement démarré"


class CheckoutResponse(BaseModel):
    paymentUrl: str


class ConfirmPaymentResponse(BaseModel):
    task: TranslationTask
    paymentPending: bool = False
    alreadyStarted: bool = False


class BubbleTransformation(BaseModel):
    order: int
    originalText: str
    translatedText: str


class PageTransformation(BaseModel):
    pageIndex: int
    imageName: str
    bubbles: list[BubbleTransformation]


class TransformationReportResponse(BaseModel):
    taskId: str
    sourceLanguage: str
    targetLanguage: str
    pages: list[PageTransformation]
    viewUrl: str


class PayDunyaWebhookPayload(BaseModel):
    token: str = Field(alias="invoice_token")
    status: str = ""

    model_config = {"populate_by_name": True}
