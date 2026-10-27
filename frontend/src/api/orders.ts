import { apiClient } from "./client";

export type FulfillmentMethod = "SHIPPING" | "STORE_PICKUP";

export interface OrderLineRequest {
  variant_id: string;
  quantity: number;
}

export interface ShippingAddressRequest {
  recipient_name: string;
  address_line_1: string;
  address_line_2?: string;
  city: string;
  region?: string;
  postal_code: string;
  country_code: string;
  phone?: string;
}

export interface CreateOrderRequest {
  lines: OrderLineRequest[];
  fulfillment_method: FulfillmentMethod;
  shipping_address?: ShippingAddressRequest;
}

export interface GuestCreateOrderRequest extends CreateOrderRequest {
  email: string;
  first_name?: string;
  last_name?: string;
  recipient_is_buyer: boolean;
}

export interface PendingOrderResponse {
  id: string;
  order_number: string;
  order_status: string;
  payment_status: string;
  currency: string;
  total_gross: string;
  reservation_expires_at: string;
  reserved_quantity: number;
  checkout_url: string;
}

export type CustomerOrderStatus =
  | "PENDING_PAYMENT"
  | "PAID"
  | "CANCELLED"
  | "EXPIRED"
  | "READY_TO_SHIP"
  | "READY_FOR_PICKUP"
  | "SHIPPED"
  | "DELIVERED"
  | "PICKED_UP"
  | "COMPLETED";

export type CustomerPaymentStatus =
  | "UNPAID"
  | "PAID"
  | "FAILED"
  | "PARTIALLY_REFUNDED"
  | "REFUNDED";

export type CustomerShipmentStatus =
  | "READY_TO_SHIP"
  | "READY_FOR_PICKUP"
  | "SHIPPED"
  | "DELIVERED"
  | "PICKED_UP"
  | "CANCELLED"
  | "FAILED";

export interface CustomerOrderSummary {
  order_id: string;
  order_number: string;
  order_status: CustomerOrderStatus;
  payment_status: CustomerPaymentStatus;
  currency: string;
  total_gross: string;
  created_at: string;
}

export interface CheckoutPaymentStatusResponse {
  order_status: CustomerOrderStatus;
  payment_status: CustomerPaymentStatus;
}

export interface OrderLineUnitDetails {
  order_line_unit_id: string;
  already_returned: boolean;
}

export interface CustomerOrderLineDetails {
  order_line_id: string;
  product_name: string;
  variant: Record<string, unknown>;
  image_url: string;
  sku: string;
  quantity: number;
  unit_price_gross: string;
  total_gross: string;
  units: OrderLineUnitDetails[];
}

export interface CustomerOrderDetails {
  order_id: string;
  order_number: string;
  channel: "ECOM" | "POS";
  order_status: CustomerOrderStatus;
  payment_status: CustomerPaymentStatus;
  currency: string;
  total_gross: string;
  created_at: string;
  paid_at: string | null;
  lines: CustomerOrderLineDetails[];
}

export interface CustomerOrderShipmentStatus {
  shipment_id: string;
  order_id: string;
  order_number: string;
  status: CustomerShipmentStatus;
  order_status: CustomerOrderStatus;
  mock_tracking_number: string | null;
  shipped_at: string | null;
  delivered_at: string | null;
  ready_for_pickup_at: string | null;
  pickup_deadline_at: string | null;
  picked_up_at: string | null;
}

export type CustomerReturnStatus =
  | "REQUESTED"
  | "APPROVED"
  | "RECEIVED"
  | "INSPECTED"
  | "COMPLETED"
  | "REJECTED";

export interface CustomerOrderReturnItem {
  order_line_unit_id: string;
  inspection_result: "PENDING" | "RESTOCK" | "REPAIR" | "DAMAGED";
}

export interface CustomerOrderReturnRefund {
  amount: string;
  method: "ORIGINAL_PAYMENT";
  status: "PENDING" | "REFUNDED" | "FAILED";
  completed_at: string | null;
}

export interface CustomerOrderReturn {
  return_id: string;
  order_id: string;
  status: CustomerReturnStatus;
  requested_outcome: "REFUND";
  reason: string;
  requested_at: string;
  completed_at: string | null;
  items: CustomerOrderReturnItem[];
  refunds: CustomerOrderReturnRefund[];
}

export interface CreateCustomerReturnRequest {
  requested_outcome: "REFUND";
  reason: string;
  items: Array<{ order_line_unit_id: string }>;
}

export interface CreateCustomerReturnResponse {
  return_id: string;
  order_id: string;
  status: "REQUESTED";
  requested_outcome: "REFUND";
  item_count: number;
}

export interface ProcessCustomerReturnResponse {
  return_id: string;
  order_id: string;
  status: "COMPLETED";
  completed_outcome: "REFUND";
  payment_status: "PARTIALLY_REFUNDED" | "REFUNDED";
  returned_item_count: number;
  financial_outcome: "REFUND";
  amount: string;
}

export const ordersApi = {
  async getCheckoutPaymentStatus(
    sessionId: string,
  ): Promise<CheckoutPaymentStatusResponse> {
    const response = await apiClient.get<CheckoutPaymentStatusResponse>(
      "/checkout/status",
      { params: { session_id: sessionId } },
    );

    return response.data;
  },

  async listCustomerOrders(): Promise<CustomerOrderSummary[]> {
    const response = await apiClient.get<CustomerOrderSummary[]>("/orders");
    return response.data;
  },

  async listCustomerPOSOrders(): Promise<CustomerOrderSummary[]> {
    const response = await apiClient.get<CustomerOrderSummary[]>("/orders/pos");
    return response.data;
  },

  async getCustomerOrderDetails(
    orderId: string,
  ): Promise<CustomerOrderDetails> {
    const response = await apiClient.get<CustomerOrderDetails>(
      `/orders/${encodeURIComponent(orderId)}`,
    );

    return response.data;
  },

  async getCustomerOrderStatus(
    orderId: string,
  ): Promise<CustomerOrderShipmentStatus> {
    const response = await apiClient.get<CustomerOrderShipmentStatus>(
      `/orders/${encodeURIComponent(orderId)}/status`,
    );

    return response.data;
  },

  async listCustomerOrderReturns(orderId: string): Promise<CustomerOrderReturn[]> {
    const response = await apiClient.get<CustomerOrderReturn[]>(
      `/orders/${encodeURIComponent(orderId)}/returns`,
    );

    return response.data;
  },

  async createCustomerReturn(
    orderId: string,
    request: CreateCustomerReturnRequest,
  ): Promise<CreateCustomerReturnResponse> {
    const response = await apiClient.post<CreateCustomerReturnResponse>(
      `/orders/${encodeURIComponent(orderId)}/returns`,
      request,
    );

    return response.data;
  },

  async processCustomerReturn(
    returnId: string,
  ): Promise<ProcessCustomerReturnResponse> {
    const response = await apiClient.post<ProcessCustomerReturnResponse>(
      `/returns/${encodeURIComponent(returnId)}/process`,
    );

    return response.data;
  },

  async createCustomerOrder(
    request: CreateOrderRequest,
    idempotencyKey: string,
  ): Promise<PendingOrderResponse> {
    const response = await apiClient.post<PendingOrderResponse>(
      "/orders",
      request,
      { headers: { "Idempotency-Key": idempotencyKey } },
    );

    return response.data;
  },

  async createGuestOrder(
    request: GuestCreateOrderRequest,
    idempotencyKey: string,
  ): Promise<PendingOrderResponse> {
    const response = await apiClient.post<PendingOrderResponse>(
      "/orders/guest",
      request,
      { headers: { "Idempotency-Key": idempotencyKey } },
    );

    return response.data;
  },
};
