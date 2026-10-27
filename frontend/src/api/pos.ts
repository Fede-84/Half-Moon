import { apiClient } from "./client";
import type {
  CustomerOrderDetails,
  CustomerOrderStatus,
  CustomerPaymentStatus,
} from "./orders";

export interface POSCheckoutLineRequest {
  variant_id: string;
  quantity: number;
}

export interface POSCashCheckoutRequest {
  lines: POSCheckoutLineRequest[];
  payment_method: "CASH";
}

export interface POSDemoCheckoutRequest {
  lines: POSCheckoutLineRequest[];
}

export interface POSCardCheckoutRequest {
  lines: POSCheckoutLineRequest[];
  payment_method: "CARD";
}

export interface POSCashCheckoutResponse {
  order_id: string;
  order_number: string;
  payment_method: "CASH";
  order_status: "COMPLETED";
  payment_status: "PAID";
  total_gross: string;
  currency: string;
}

export interface POSCardCheckoutResponse {
  order_id: string;
  order_number: string;
  payment_method: "CARD";
  order_status: "PENDING_PAYMENT";
  payment_status: "UNPAID";
  total_gross: string;
  currency: string;
  reservation_expires_at: string;
  payment_intent_client_secret: string;
}

export interface POSOrderStatusResponse {
  order_id: string;
  order_number: string;
  payment_method: "CASH" | "CARD";
  order_status: CustomerOrderStatus;
  payment_status: CustomerPaymentStatus;
  total_gross: string;
  currency: string;
  reservation_expires_at: string | null;
}

export interface POSReceiptResponse {
  order_id: string;
  order_number: string;
  claim_code: string | null;
  claim_expires_at: string | null;
}

export interface POSReceiptAcknowledgementResponse {
  acknowledged: true;
  acknowledged_at: string;
}

export interface POSOrderClaimRequest {
  order_number: string;
  claim_code: string;
}

export interface POSOrderClaimResponse {
  claimed: true;
  order_id: string;
  order_number: string;
  claimed_at: string;
}

export interface POSReturnRequest {
  requested_outcome: "REFUND";
  reason: string;
  items: Array<{ order_line_unit_id: string }>;
}

export interface POSReturnResponse {
  return_id: string;
  order_id: string;
  status: "COMPLETED";
  completed_outcome: "REFUND";
  payment_status: "PARTIALLY_REFUNDED" | "REFUNDED";
  returned_item_count: number;
  financial_outcome: "REFUND";
  amount: string;
}

export interface POSClaimResetResponse {
  reset: true;
  failed_attempt_count: 0;
  reset_at: string;
}

export const posApi = {
  async createDemoCashCheckout(
    request: POSDemoCheckoutRequest,
    idempotencyKey: string,
  ): Promise<POSCashCheckoutResponse> {
    const response = await apiClient.post<POSCashCheckoutResponse>(
      "/orders/pos-demo",
      request,
      { headers: { "Idempotency-Key": idempotencyKey } },
    );
    return response.data;
  },

  async getDemoReceipt(orderId: string): Promise<POSReceiptResponse> {
    const response = await apiClient.get<POSReceiptResponse>(
      `/orders/pos-demo/${encodeURIComponent(orderId)}/receipt`,
    );
    return response.data;
  },

  async acknowledgeDemoReceipt(
    orderId: string,
  ): Promise<POSReceiptAcknowledgementResponse> {
    const response = await apiClient.post<POSReceiptAcknowledgementResponse>(
      `/orders/pos-demo/${encodeURIComponent(orderId)}/receipt/acknowledgement`,
    );
    return response.data;
  },

  async claimCustomerOrder(
    request: POSOrderClaimRequest,
  ): Promise<POSOrderClaimResponse> {
    const response = await apiClient.post<POSOrderClaimResponse>(
      "/orders/pos-claim",
      request,
    );
    return response.data;
  },

  async getStaffOrderDetails(
    orderNumber: string,
    signal?: AbortSignal,
  ): Promise<CustomerOrderDetails> {
    const response = await apiClient.get<CustomerOrderDetails>(
      "/staff/pos/orders/lookup",
      {
        params: { order_number: orderNumber },
        signal,
      },
    );
    return response.data;
  },

  async processStaffReturn(
    orderId: string,
    request: POSReturnRequest,
  ): Promise<POSReturnResponse> {
    const response = await apiClient.post<POSReturnResponse>(
      `/staff/pos/orders/${encodeURIComponent(orderId)}/returns`,
      request,
    );
    return response.data;
  },

  async processCustomerReturn(
    orderId: string,
    request: POSReturnRequest,
  ): Promise<POSReturnResponse> {
    const response = await apiClient.post<POSReturnResponse>(
      `/orders/pos/${encodeURIComponent(orderId)}/returns`,
      request,
    );
    return response.data;
  },

  async resetClaimByOrderNumber(
    orderNumber: string,
  ): Promise<POSClaimResetResponse> {
    const response = await apiClient.post<POSClaimResetResponse>(
      "/staff/pos/orders/claim/reset",
      { order_number: orderNumber },
    );
    return response.data;
  },

  async createCashCheckout(
    request: POSCashCheckoutRequest,
    idempotencyKey: string,
  ): Promise<POSCashCheckoutResponse> {
    const response = await apiClient.post<POSCashCheckoutResponse>(
      "/staff/pos/checkout",
      request,
      { headers: { "Idempotency-Key": idempotencyKey } },
    );
    return response.data;
  },

  async createCardCheckout(
    request: POSCardCheckoutRequest,
    idempotencyKey: string,
  ): Promise<POSCardCheckoutResponse> {
    const response = await apiClient.post<POSCardCheckoutResponse>(
      "/staff/pos/checkout",
      request,
      { headers: { "Idempotency-Key": idempotencyKey } },
    );
    return response.data;
  },

  async getOrderStatus(
    orderId: string,
    signal?: AbortSignal,
  ): Promise<POSOrderStatusResponse> {
    const response = await apiClient.get<POSOrderStatusResponse>(
      `/staff/pos/orders/${encodeURIComponent(orderId)}/status`,
      { signal },
    );
    return response.data;
  },

  async getReceipt(orderId: string): Promise<POSReceiptResponse> {
    const response = await apiClient.get<POSReceiptResponse>(
      `/staff/pos/orders/${encodeURIComponent(orderId)}/receipt`,
    );
    return response.data;
  },

  async acknowledgeReceipt(
    orderId: string,
  ): Promise<POSReceiptAcknowledgementResponse> {
    const response =
      await apiClient.post<POSReceiptAcknowledgementResponse>(
        `/staff/pos/orders/${encodeURIComponent(orderId)}/receipt/acknowledgement`,
      );
    return response.data;
  },
};
