import { apiClient } from "./client";

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export interface StaffTokenResponse extends TokenResponse {
  role: string;
}

export interface CustomerProfile {
  id: string;
  email: string;
  first_name: string | null;
  last_name: string | null;
}

export interface StaffProfile {
  id: string;
  email: string;
  first_name: string;
  last_name: string;
  role: string;
}

export interface CustomerRegisterRequest {
  email: string;
  password: string;
  first_name: string;
  last_name: string;
}

export interface CustomerRegisterResponse {
  id: string;
  message: string;
}

export interface AuthMessageResponse {
  message: string;
}

export interface EmailVerificationResponse extends AuthMessageResponse {
  verified: true;
}

export const authApi = {
  async registerCustomer(
    request: CustomerRegisterRequest,
  ): Promise<CustomerRegisterResponse> {
    const response = await apiClient.post<CustomerRegisterResponse>(
      "/auth/register",
      request,
    );

    return response.data;
  },

  async loginCustomer(email: string, password: string): Promise<TokenResponse> {
    const response = await apiClient.post<TokenResponse>("/auth/login", {
      email,
      password,
    });

    return response.data;
  },

  async loginStaff(email: string, password: string): Promise<StaffTokenResponse> {
    const response = await apiClient.post<StaffTokenResponse>(
      "/auth/staff/login",
      { email, password },
    );

    return response.data;
  },

  async getCustomerProfile(): Promise<CustomerProfile> {
    const response = await apiClient.get<CustomerProfile>("/auth/me");
    return response.data;
  },

  async getStaffProfile(): Promise<StaffProfile> {
    const response = await apiClient.get<StaffProfile>("/staff/me");
    return response.data;
  },

  async logout(): Promise<void> {
    await apiClient.post("/auth/logout");
  },

  async verifyCustomerEmail(
    token: string,
  ): Promise<EmailVerificationResponse> {
    const response = await apiClient.get<EmailVerificationResponse>(
      "/auth/verify-email",
      { params: { token } },
    );

    return response.data;
  },

  async requestPasswordReset(email: string): Promise<AuthMessageResponse> {
    const response = await apiClient.post<AuthMessageResponse>(
      "/auth/password-reset/request",
      { email },
    );

    return response.data;
  },

  async confirmPasswordReset(
    token: string,
    newPassword: string,
  ): Promise<AuthMessageResponse> {
    const response = await apiClient.post<AuthMessageResponse>(
      "/auth/password-reset/confirm",
      { token, new_password: newPassword },
    );

    return response.data;
  },
};
