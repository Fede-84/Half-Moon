import { apiClient } from "./client";

export interface CatalogProductVariantSummary {
  id: string;
  color: string;
  size_system: string | null;
  size_label: string;
  retail_price: string;
  image_url: string | null;
}

export interface CatalogProductSummary {
  id: string;
  name: string;
  slug: string;
  description: string;
  category: string;
  image_url: string;
  variants: CatalogProductVariantSummary[];
}

export interface CatalogProductVariantDetail
  extends CatalogProductVariantSummary {
  product_id: string;
  material: string;
  sku: string;
  ean: string;
  available_stock: number;
  locations: Array<{
    location_code: string;
    available: number;
  }>;
}

export interface CatalogProductDetail
  extends Omit<CatalogProductSummary, "variants"> {
  variants: CatalogProductVariantDetail[];
}

export const catalogApi = {
  async listProducts(signal?: AbortSignal): Promise<CatalogProductSummary[]> {
    const response = await apiClient.get<CatalogProductSummary[]>("/products", {
      signal,
    });
    return response.data;
  },

  async getProductDetail(
    productId: string,
    signal?: AbortSignal,
  ): Promise<CatalogProductDetail> {
    const response = await apiClient.get<CatalogProductDetail>(
      `/products/${encodeURIComponent(productId)}/detail`,
      { signal },
    );
    return response.data;
  },
};
