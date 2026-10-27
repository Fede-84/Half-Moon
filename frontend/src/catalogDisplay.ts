const CATEGORY_LABELS = new Map<string, string>([
  ["BRACELET", "Bracelets"],
  ["EARRINGS", "Earrings"],
  ["NECKLACE", "Necklaces"],
  ["RING", "Rings"],
]);

const ITALIAN_RING_SIZES_BY_EU_SIZE = new Map<string, string>([
  ["50", "10"],
  ["52", "12"],
  ["54", "14"],
  ["56", "16"],
  ["58", "18"],
  ["60", "20"],
]);

interface VariantDisplayFields {
  category?: unknown;
  material?: unknown;
  color?: unknown;
  size_system?: unknown;
  size_label?: unknown;
}

export function displayColor(color: string): string {
  return color === "Yellow Gold" ? "Gold" : color;
}

export function displaySize(sizeSystem: string | null, sizeLabel: string): string {
  if (sizeSystem === "EU") {
    const italianSize = ITALIAN_RING_SIZES_BY_EU_SIZE.get(sizeLabel);
    if (italianSize) return italianSize;
  }

  return sizeSystem ? `${sizeSystem} ${sizeLabel}` : sizeLabel;
}

export function shouldDisplaySize(
  category: unknown,
  sizeSystem: string | null,
): boolean {
  return category === "RING" || (category === undefined && sizeSystem === "EU");
}

export function formatVariantDescription(
  variant: VariantDisplayFields,
  separator = ", ",
): string {
  const parts: string[] = [];
  const material =
    typeof variant.material === "string"
      ? displayColor(variant.material)
      : null;
  const color =
    typeof variant.color === "string" ? displayColor(variant.color) : null;

  if (material && material !== color) parts.push(material);
  if (color) parts.push(color);

  const sizeSystem =
    typeof variant.size_system === "string" ? variant.size_system : null;
  const sizeLabel =
    typeof variant.size_label === "string" ? variant.size_label : null;
  if (shouldDisplaySize(variant.category, sizeSystem)) {
    const size =
      sizeLabel !== null ? displaySize(sizeSystem, sizeLabel) : sizeSystem;
    if (size) parts.push(size);
  }

  return parts.length > 0
    ? parts.join(separator)
    : "Variant details unavailable";
}

export function displayOrderSku(
  sku: string,
  variant: VariantDisplayFields,
): string {
  if (variant.size_system !== "EU" || typeof variant.size_label !== "string") {
    return sku;
  }

  const italianSize = ITALIAN_RING_SIZES_BY_EU_SIZE.get(variant.size_label);
  if (!italianSize) return sku;

  const legacySizeSuffix = `-EU${variant.size_label}`;
  if (!sku.endsWith(legacySizeSuffix)) return sku;

  return `${sku.slice(0, -legacySizeSuffix.length)}-${italianSize}`;
}

export function displayCategory(category: string): string {
  const knownLabel = CATEGORY_LABELS.get(category.toUpperCase());
  if (knownLabel) return knownLabel;

  return category
    .toLowerCase()
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function formatEuroPrice(price: string): string {
  const priceParts = /^(\d+)(?:\.(\d{1,2}))?$/.exec(price);
  if (!priceParts) return "Price unavailable";

  const wholeEuros = BigInt(priceParts[1]);
  const fraction = (priceParts[2] ?? "").padEnd(2, "0");
  const formatter = new Intl.NumberFormat("en-IE", {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  return formatter
    .formatToParts(wholeEuros)
    .map((part) => (part.type === "fraction" ? fraction : part.value))
    .join("");
}

export function formatEuroCents(amountCents: bigint): string {
  const wholeEuros = amountCents / 100n;
  const fraction = (amountCents % 100n).toString().padStart(2, "0");
  const formatter = new Intl.NumberFormat("en-IE", {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  return formatter
    .formatToParts(wholeEuros)
    .map((part) => (part.type === "fraction" ? fraction : part.value))
    .join("");
}
