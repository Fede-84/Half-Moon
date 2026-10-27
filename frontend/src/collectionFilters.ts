export interface CollectionFilterSelection {
  categories: string[];
  colors: string[];
}

const COLLECTION_CATEGORY_PARAMETER = "collectionCategory";
const COLLECTION_COLOR_PARAMETER = "collectionColor";

export function readCollectionFilters(
  searchParams: URLSearchParams,
  availableCategories: string[],
  availableColors: string[],
): CollectionFilterSelection {
  const requestedCategories = [
    ...new Set(searchParams.getAll(COLLECTION_CATEGORY_PARAMETER)),
  ];
  const requestedColors = [
    ...new Set(searchParams.getAll(COLLECTION_COLOR_PARAMETER)),
  ];

  return {
    categories:
      availableCategories.length === 0
        ? requestedCategories
        : requestedCategories.filter((category) =>
            availableCategories.includes(category),
          ),
    colors: requestedColors.filter((color) =>
      availableColors.includes(color),
    ),
  };
}

export function updateCollectionFilterParams(
  searchParams: URLSearchParams,
  filters: CollectionFilterSelection,
): URLSearchParams {
  const nextSearchParams = new URLSearchParams(searchParams);
  nextSearchParams.delete(COLLECTION_CATEGORY_PARAMETER);
  nextSearchParams.delete(COLLECTION_COLOR_PARAMETER);

  for (const category of filters.categories) {
    nextSearchParams.append(COLLECTION_CATEGORY_PARAMETER, category);
  }

  for (const color of filters.colors) {
    nextSearchParams.append(COLLECTION_COLOR_PARAMETER, color);
  }

  return nextSearchParams;
}

export function createProductDetailHref(
  productSlug: string,
  productColor: string,
  filters: CollectionFilterSelection,
): string {
  const searchParams = new URLSearchParams({ color: productColor });

  for (const category of filters.categories) {
    searchParams.append(COLLECTION_CATEGORY_PARAMETER, category);
  }

  for (const color of filters.colors) {
    searchParams.append(COLLECTION_COLOR_PARAMETER, color);
  }

  return `/products/${encodeURIComponent(productSlug)}?${searchParams.toString()}`;
}

export function createCollectionReturnHref(
  productSearchParams: URLSearchParams,
): string {
  const collectionSearchParams = new URLSearchParams();

  for (const category of productSearchParams.getAll(
    COLLECTION_CATEGORY_PARAMETER,
  )) {
    collectionSearchParams.append(COLLECTION_CATEGORY_PARAMETER, category);
  }

  for (const color of productSearchParams.getAll(COLLECTION_COLOR_PARAMETER)) {
    collectionSearchParams.append(COLLECTION_COLOR_PARAMETER, color);
  }

  const query = collectionSearchParams.toString();
  return `/${query ? `?${query}` : ""}#collection-title`;
}
