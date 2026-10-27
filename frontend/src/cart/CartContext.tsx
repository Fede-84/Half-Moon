import {
  createContext,
  useContext,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { catalogApi } from "../api/catalog";

const CART_STORAGE_KEY = "half_moon_ecommerce_cart";
const CHECKOUT_PENDING_STORAGE_KEY = "half_moon_ecommerce_checkout_pending";
const CART_STORAGE_ERROR =
  "This tab could not save your bag for recovery if checkout is cancelled.";
const CART_RESTORE_ERROR =
  "Your saved bag could not be restored. Please review your bag before continuing.";

export interface CartItemInput {
  productId: string;
  productSlug: string;
  productName: string;
  productCategory: string;
  variantId: string;
  color: string;
  material: string;
  sizeSystem: string | null;
  sizeLabel: string;
  retailPrice: string;
  imageUrl: string;
  availableStock: number;
}

export interface CartItem extends Omit<CartItemInput, "productCategory"> {
  productCategory?: string;
  quantity: number;
}

interface CartContextValue {
  items: CartItem[];
  pieceCount: number;
  persistenceError: string | null;
  addItem: (item: CartItemInput) => boolean;
  changeQuantity: (variantId: string, quantity: number) => void;
  removeItem: (variantId: string) => void;
  markCheckoutPending: () => void;
  cancelCheckout: () => void;
  completeCheckout: () => void;
}

interface CartRestoreState {
  items: CartItem[];
  error: string | null;
}

function isCartItem(value: unknown): value is CartItem {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }

  return (
    "productId" in value &&
    typeof value.productId === "string" &&
    "productSlug" in value &&
    typeof value.productSlug === "string" &&
    "productName" in value &&
    typeof value.productName === "string" &&
    (!("productCategory" in value) ||
      typeof value.productCategory === "string") &&
    "variantId" in value &&
    typeof value.variantId === "string" &&
    "color" in value &&
    typeof value.color === "string" &&
    "material" in value &&
    typeof value.material === "string" &&
    "sizeSystem" in value &&
    (typeof value.sizeSystem === "string" || value.sizeSystem === null) &&
    "sizeLabel" in value &&
    typeof value.sizeLabel === "string" &&
    "retailPrice" in value &&
    typeof value.retailPrice === "string" &&
    "imageUrl" in value &&
    typeof value.imageUrl === "string" &&
    "availableStock" in value &&
    typeof value.availableStock === "number" &&
    Number.isInteger(value.availableStock) &&
    value.availableStock >= 0 &&
    "quantity" in value &&
    typeof value.quantity === "number" &&
    Number.isInteger(value.quantity) &&
    value.quantity > 0
  );
}

function clearStoredCart(): void {
  try {
    window.sessionStorage.removeItem(CART_STORAGE_KEY);
  } catch (error: unknown) {
    console.error("The invalid saved cart could not be removed.", error);
  }
}

function readStoredCart(): CartRestoreState {
  let serializedItems: string | null;

  try {
    serializedItems = window.sessionStorage.getItem(CART_STORAGE_KEY);
  } catch (error: unknown) {
    console.error("The saved cart could not be read from this tab.", error);
    return { items: [], error: CART_STORAGE_ERROR };
  }

  if (serializedItems === null) {
    return { items: [], error: null };
  }

  let parsedItems: unknown;

  try {
    parsedItems = JSON.parse(serializedItems);
  } catch (error: unknown) {
    console.error("The saved cart contains invalid JSON.", error);
    clearStoredCart();
    return { items: [], error: CART_RESTORE_ERROR };
  }

  if (!Array.isArray(parsedItems) || !parsedItems.every(isCartItem)) {
    console.error("The saved cart has an invalid data shape.");
    clearStoredCart();
    return { items: [], error: CART_RESTORE_ERROR };
  }

  return { items: parsedItems, error: null };
}

const CartContext = createContext<CartContextValue | null>(null);

export function CartProvider({ children }: { children: ReactNode }) {
  const [restoredCart] = useState(readStoredCart);
  const [items, setItems] = useState<CartItem[]>(restoredCart.items);
  const [persistenceError, setPersistenceError] = useState<string | null>(
    restoredCart.error,
  );
  const itemsRef = useRef<CartItem[]>(restoredCart.items);
  const hasLegacyItemsWithoutCategory = items.some(
    (item) => item.productCategory === undefined,
  );

  const replaceItems = useCallback((nextItems: CartItem[]): void => {
    itemsRef.current = nextItems;
    setItems(nextItems);

    try {
      if (nextItems.length === 0) {
        window.sessionStorage.removeItem(CART_STORAGE_KEY);
      } else {
        window.sessionStorage.setItem(
          CART_STORAGE_KEY,
          JSON.stringify(nextItems),
        );
      }
      setPersistenceError(null);
    } catch (error: unknown) {
      console.error("The cart could not be saved to this tab.", error);
      setPersistenceError(CART_STORAGE_ERROR);
    }
  }, []);

  useEffect(() => {
    if (!hasLegacyItemsWithoutCategory) {
      return;
    }

    const controller = new AbortController();

    async function restoreProductCategories(): Promise<void> {
      try {
        const products = await catalogApi.listProducts(controller.signal);
        if (controller.signal.aborted) {
          return;
        }

        const categoriesByProductId = new Map(
          products.map((product) => [product.id, product.category]),
        );
        const unresolvedProductIds: string[] = [];
        let didRestoreCategory = false;
        const updatedItems = itemsRef.current.map((item) => {
          if (item.productCategory !== undefined) {
            return item;
          }

          const productCategory = categoriesByProductId.get(item.productId);
          if (!productCategory) {
            unresolvedProductIds.push(item.productId);
            return item;
          }

          didRestoreCategory = true;
          return { ...item, productCategory };
        });

        if (didRestoreCategory) {
          replaceItems(updatedItems);
        }

        if (unresolvedProductIds.length > 0) {
          console.error(
            "Some saved cart product categories could not be restored.",
            unresolvedProductIds,
          );
        }
      } catch (error: unknown) {
        if (!controller.signal.aborted) {
          console.error(
            "Saved cart product categories could not be loaded.",
            error,
          );
        }
      }
    }

    void restoreProductCategories();

    return () => controller.abort();
  }, [hasLegacyItemsWithoutCategory, replaceItems]);

  const markCheckoutPending = useCallback((): void => {
    try {
      window.sessionStorage.setItem(CHECKOUT_PENDING_STORAGE_KEY, "true");
    } catch (error: unknown) {
      console.error("The pending checkout could not be recorded.", error);
      setPersistenceError(CART_STORAGE_ERROR);
    }
  }, []);

  const cancelCheckout = useCallback((): void => {
    try {
      window.sessionStorage.removeItem(CHECKOUT_PENDING_STORAGE_KEY);
    } catch (error: unknown) {
      console.error("The cancelled checkout marker could not be removed.", error);
      setPersistenceError(CART_STORAGE_ERROR);
    }
  }, []);

  const completeCheckout = useCallback((): void => {
    try {
      if (
        window.sessionStorage.getItem(CHECKOUT_PENDING_STORAGE_KEY) !== "true"
      ) {
        return;
      }

      window.sessionStorage.removeItem(CHECKOUT_PENDING_STORAGE_KEY);
    } catch (error: unknown) {
      console.error("The completed checkout could not be finalized.", error);
      setPersistenceError(CART_STORAGE_ERROR);
      return;
    }

    replaceItems([]);
  }, [replaceItems]);

  function addItem(item: CartItemInput): boolean {
    const currentItems = itemsRef.current;
    const existingItem = currentItems.find(
      (cartItem) => cartItem.variantId === item.variantId,
    );
    const currentQuantity = existingItem?.quantity ?? 0;

    if (item.availableStock <= currentQuantity) {
      return false;
    }

    if (existingItem) {
      replaceItems(
        currentItems.map((cartItem) =>
          cartItem.variantId === item.variantId
            ? { ...item, quantity: cartItem.quantity + 1 }
            : cartItem,
        ),
      );
      return true;
    }

    replaceItems([...currentItems, { ...item, quantity: 1 }]);
    return true;
  }

  function changeQuantity(variantId: string, quantity: number): void {
    if (!Number.isInteger(quantity) || quantity < 1) {
      return;
    }

    const currentItem = itemsRef.current.find(
      (cartItem) => cartItem.variantId === variantId,
    );

    if (!currentItem || quantity > currentItem.availableStock) {
      return;
    }

    replaceItems(
      itemsRef.current.map((cartItem) =>
        cartItem.variantId === variantId
          ? { ...cartItem, quantity }
          : cartItem,
      ),
    );
  }

  function removeItem(variantId: string): void {
    replaceItems(
      itemsRef.current.filter((item) => item.variantId !== variantId),
    );
  }

  const pieceCount = items.reduce(
    (totalQuantity, item) => totalQuantity + item.quantity,
    0,
  );

  return (
    <CartContext.Provider
      value={{
        items,
        pieceCount,
        persistenceError,
        addItem,
        changeQuantity,
        removeItem,
        markCheckoutPending,
        cancelCheckout,
        completeCheckout,
      }}
    >
      {children}
    </CartContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useCart(): CartContextValue {
  const cart = useContext(CartContext);

  if (cart === null) {
    throw new Error("useCart must be used within CartProvider");
  }

  return cart;
}
