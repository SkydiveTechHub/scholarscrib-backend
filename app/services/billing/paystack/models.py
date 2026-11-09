"""Pydantic shapes for Paystack initialize / verify / webhook / charge / plans."""

from enum import StrEnum

from pydantic import BaseModel, Field


class PaystackTransactionData(BaseModel):
    authorization_url: str | None = None
    access_code: str | None = None
    reference: str | None = None


class PaystackMessage(BaseModel):
    message: str | None = None
    status: bool = False


class PaystackTransaction(PaystackMessage):
    data: PaystackTransactionData | None = None


class PaystackAuthorization(BaseModel):
    authorization_code: str | None = None
    bin: str | None = None
    last4: str | None = None
    exp_month: str | None = None
    exp_year: str | None = None
    card_type: str | None = None
    bank: str | None = None
    brand: str | None = None
    reusable: bool | None = None


class PaystackCustomer(BaseModel):
    id: int | None = None
    email: str | None = None
    customer_code: str | None = None


class PaystackVerifyData(BaseModel):
    status: str | None = None
    amount: int | None = None
    currency: str | None = None
    channel: str | None = None
    reference: str | None = None
    authorization: PaystackAuthorization | None = None
    customer: PaystackCustomer | None = None
    metadata: dict | None = None


class PaystackTransactionVerify(PaystackMessage):
    data: PaystackVerifyData | None = None


class PaystackChargeData(BaseModel):
    status: str | None = None
    amount: int | None = None
    currency: str | None = None
    reference: str | None = None
    authorization: PaystackAuthorization | None = None
    customer: PaystackCustomer | None = None


class PaystackChargeResponse(PaystackMessage):
    data: PaystackChargeData | None = None


class PaystackPlanData(BaseModel):
    name: str | None = None
    plan_code: str | None = None
    amount: int | None = None
    interval: str | None = None
    currency: str | None = None
    id: int | None = None


class PaystackPlanResponse(PaystackMessage):
    data: PaystackPlanData | None = None


class PaystackPlanListResponse(PaystackMessage):
    data: list[PaystackPlanData] = Field(default_factory=list)


class PaystackSubscriptionData(BaseModel):
    subscription_code: str | None = None
    email_token: str | None = None
    status: str | None = None
    amount: int | None = None
    next_payment_date: str | None = None
    customer: PaystackCustomer | dict | None = None
    plan: PaystackPlanData | dict | None = None
    authorization: PaystackAuthorization | dict | None = None
    id: int | None = None


class PaystackSubscriptionResponse(PaystackMessage):
    data: PaystackSubscriptionData | None = None


class PaystackManageLinkData(BaseModel):
    link: str | None = None


class PaystackManageLinkResponse(PaystackMessage):
    data: PaystackManageLinkData | None = None


class PaystackEvents(StrEnum):
    CHARGE_SUCCESS = "charge.success"
    INVOICE_CREATE = "invoice.create"
    INVOICE_UPDATE = "invoice.update"
    INVOICE_PAYMENT_FAILED = "invoice.payment_failed"
    SUBSCRIPTION_CREATE = "subscription.create"
    SUBSCRIPTION_NOT_RENEW = "subscription.not_renew"
    SUBSCRIPTION_DISABLE = "subscription.disable"
    SUBSCRIPTION_EXPIRING_CARDS = "subscription.expiring_cards"


class PaystackWebhookData(BaseModel):
    event: str
    data: dict = Field(default_factory=dict)
