import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Link,
  useLocation,
  useNavigate,
  useSearchParams,
} from "react-router-dom";
import {
  catalogApi,
  type CatalogProductSummary,
  type CatalogProductVariantSummary,
} from "./api/catalog";
import { useCart } from "./cart/CartContext";
import {
  createCollectionReturnHref,
  createProductDetailHref,
  readCollectionFilters,
  updateCollectionFilterParams,
  type CollectionFilterSelection,
} from "./collectionFilters";
import {
  displayCategory,
  displayColor,
  displaySize,
  formatEuroCents,
  formatEuroPrice,
  shouldDisplaySize,
} from "./catalogDisplay";
import { useAuth } from "./features/auth/AuthProvider";
import styles from "./Storefront.module.css";

type ProductVariant = CatalogProductVariantSummary;
type Product = CatalogProductSummary;

interface ColourOption {
  apiColor: string;
  label: string;
}

interface ColourCard {
  product: Product;
  variant: ProductVariant;
  apiColor: string;
  displayColor: string;
  imageUrl: string;
}

type DrawerName = "filters" | "cart" | null;

const COLOUR_OPTIONS: ColourOption[] = [
  { apiColor: "Yellow Gold", label: "Gold" },
  { apiColor: "Silver", label: "Silver" },
];
const FILTER_COLORS = COLOUR_OPTIONS.map((option) => option.apiColor);

const PRODUCT_CATEGORY_ORDER = new Map<string, number>([
  ["NECKLACE", 0],
  ["EARRINGS", 1],
  ["RING", 2],
  ["BRACELET", 3],
]);

function createColourCards(products: Product[]): ColourCard[] {
  const cards: ColourCard[] = [];
  const orderedProducts = [...products].sort(
    (left, right) =>
      (PRODUCT_CATEGORY_ORDER.get(left.category) ??
        PRODUCT_CATEGORY_ORDER.size) -
      (PRODUCT_CATEGORY_ORDER.get(right.category) ??
        PRODUCT_CATEGORY_ORDER.size),
  );

  for (const product of orderedProducts) {
    for (const colourOption of COLOUR_OPTIONS) {
      const variant = product.variants.find(
        (productVariant) => productVariant.color === colourOption.apiColor,
      );

      if (!variant) continue;

      cards.push({
        product,
        variant,
        apiColor: colourOption.apiColor,
        displayColor: colourOption.label,
        imageUrl: variant.image_url ?? product.image_url,
      });
    }
  }

  return cards;
}

function toggleSelection(values: string[], selectedValue: string): string[] {
  if (values.includes(selectedValue)) {
    return values.filter((value) => value !== selectedValue);
  }

  return [...values, selectedValue];
}

function MenuIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className={styles.icon}>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className={styles.icon}>
      <circle cx="10.8" cy="10.8" r="6.8" />
      <path d="m16 16 4 4" />
    </svg>
  );
}

function AccountIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className={styles.icon}>
      <circle cx="12" cy="8" r="3.5" />
      <path d="M4.5 20c.7-4 3.3-6 7.5-6s6.8 2 7.5 6" />
    </svg>
  );
}

function CartIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className={styles.icon}>
      <path d="M3 4h2l2.2 11.2a2 2 0 0 0 2 1.6h8.4a2 2 0 0 0 2-1.6L21 8H6" />
      <circle cx="10" cy="20" r="1" />
      <circle cx="18" cy="20" r="1" />
    </svg>
  );
}

type StorefrontHeaderProps =
  | {
      mode: "home";
      onOpenFilters: () => void;
      onOpenCart: () => void;
    }
  | {
      mode: "product";
      onOpenCart: () => void;
      backToCollectionHref: string;
    };

export function StorefrontHeader(props: StorefrontHeaderProps) {
  const { pieceCount } = useCart();
  const { session } = useAuth();

  return (
    <header className={styles.header}>
      {props.mode === "home" ? (
        <button
          className={styles.iconButton}
          type="button"
          aria-label="Open collection filters"
          onClick={props.onOpenFilters}
        >
          <MenuIcon />
        </button>
      ) : (
        <Link
          className={styles.backToCollection}
          to={props.backToCollectionHref}
          state={{ scrollToCollectionImmediately: true }}
        >
          Back to collection
        </Link>
      )}

      <Link className={styles.brand} to="/" aria-label="Half Moon home">
        <span>Half Moon</span>
        <img src="/images/logo2.jpg" alt="" />
      </Link>

      <nav className={styles.headerActions} aria-label="Customer actions">
        <Link className={styles.backToCollection} to="/pos-demo">
          Try POS demo
        </Link>
        <Link
          className={styles.iconButton}
          to="/account"
          aria-label={
            session
              ? "Open customer account, signed in"
              : "Open customer account"
          }
        >
          <AccountIcon />
          {session && (
            <span
              className={styles.accountStatusDot}
              aria-hidden="true"
            />
          )}
        </Link>
        <button
          className={styles.iconButton}
          type="button"
          aria-label={`Open shopping bag, ${pieceCount} pieces`}
          onClick={props.onOpenCart}
        >
          <CartIcon />
          {pieceCount > 0 && (
            <span className={styles.cartBadge} aria-hidden="true">
              {pieceCount}
            </span>
          )}
        </button>
      </nav>
    </header>
  );
}

export function StorefrontFooter() {
  return (
    <footer className={styles.footer}>
      <img src="/images/logo2.jpg" alt="Half Moon" />
      <p>© {new Date().getFullYear()} Half Moon</p>
    </footer>
  );
}

function decimalPriceToCents(price: string): bigint | null {
  const priceParts = /^(\d+)(?:\.(\d{1,2}))?$/.exec(price);
  if (!priceParts) return null;

  return (
    BigInt(priceParts[1]) * 100n +
    BigInt((priceParts[2] ?? "").padEnd(2, "0"))
  );
}

export function CartContent({ onClose }: { onClose: () => void }) {
  const [searchParams] = useSearchParams();
  const {
    items,
    changeQuantity,
    removeItem,
    persistenceError,
  } = useCart();
  const continueShoppingHref = createCollectionReturnHref(searchParams);
  const subtotalCents = items.reduce<bigint | null>((subtotal, item) => {
    const priceCents = decimalPriceToCents(item.retailPrice);
    if (subtotal === null || priceCents === null) return null;

    return subtotal + priceCents * BigInt(item.quantity);
  }, 0n);

  return (
    <div className={styles.drawerContent}>
      <div className={styles.drawerHeader}>
        <div>
          <p className={styles.eyebrow}>Your selection</p>
          <h2 id="cart-title">Shopping bag</h2>
        </div>
        <button
          className={styles.closeButton}
          type="button"
          aria-label="Close shopping bag"
          onClick={onClose}
        >
          ×
        </button>
      </div>

      {persistenceError && (
        <p className={styles.errorMessage} role="alert">
          {persistenceError}
        </p>
      )}

      {items.length === 0 ? (
        <div className={styles.emptyCart}>
          <p>Your bag is empty.</p>
          <Link
            className={styles.primaryButton}
            to={continueShoppingHref}
            state={{ scrollToCollectionImmediately: true }}
            onClick={onClose}
          >
            Continue shopping
          </Link>
        </div>
      ) : (
        <>
          <div className={styles.cartItems}>
            {items.map((item) => (
              <article className={styles.cartLine} key={item.variantId}>
                <img
                  className={styles.cartThumbnail}
                  src={item.imageUrl}
                  alt=""
                />
                <div className={styles.cartLineInformation}>
                  <h3>{item.productName}</h3>
                  <p>{displayColor(item.color)}</p>
                  {shouldDisplaySize(
                    item.productCategory,
                    item.sizeSystem,
                  ) && (
                    <p>Size {displaySize(item.sizeSystem, item.sizeLabel)}</p>
                  )}
                  <p>{formatEuroPrice(item.retailPrice)}</p>
                  <div className={styles.quantityControls}>
                    <button
                      className={styles.quantityButton}
                      type="button"
                      aria-label={`Decrease quantity of ${item.productName}`}
                      disabled={item.quantity <= 1}
                      onClick={() =>
                        changeQuantity(item.variantId, item.quantity - 1)
                      }
                    >
                      −
                    </button>
                    <span>Quantity: {item.quantity}</span>
                    <button
                      className={styles.quantityButton}
                      type="button"
                      aria-label={`Increase quantity of ${item.productName}`}
                      disabled={item.quantity >= item.availableStock}
                      onClick={() =>
                        changeQuantity(item.variantId, item.quantity + 1)
                      }
                    >
                      +
                    </button>
                    <button
                      className={styles.removeButton}
                      type="button"
                      onClick={() => removeItem(item.variantId)}
                    >
                      Remove
                    </button>
                  </div>
                </div>
              </article>
            ))}
          </div>

          <div className={styles.cartSummary}>
            <p>
              <span>Subtotal</span>
              <strong>
                {subtotalCents === null
                  ? "Price unavailable"
                  : formatEuroCents(subtotalCents)}
              </strong>
            </p>
            <Link
              className={`${styles.primaryButton} ${styles.checkoutLink}`}
              to="/checkout"
              onClick={onClose}
            >
              Checkout
            </Link>
            <Link
              className={styles.textButton}
              to={continueShoppingHref}
              state={{ scrollToCollectionImmediately: true }}
              onClick={onClose}
            >
              Continue shopping
            </Link>
          </div>
        </>
      )}
    </div>
  );
}

function StorefrontPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [products, setProducts] = useState<Product[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [drawer, setDrawer] = useState<DrawerName>(null);
  const [draftFilters, setDraftFilters] =
    useState<CollectionFilterSelection>(() =>
      readCollectionFilters(searchParams, [], FILTER_COLORS),
    );
  const collectionHeadingRef = useRef<HTMLHeadingElement>(null);
  const drawerRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const controller = new AbortController();

    catalogApi
      .listProducts(controller.signal)
      .then((catalogProducts) => {
        setProducts(catalogProducts);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;

        setLoadError(
          error instanceof Error
            ? error.message
            : "The collection could not be loaded.",
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      });

    return () => controller.abort();
  }, [loadAttempt]);

  useLayoutEffect(() => {
    if (
      location.hash !== "#collection-title" &&
      location.hash !== "#product-grid"
    ) {
      return;
    }

    if (isLoading) return;

    const isProductReturn =
      typeof location.state === "object" &&
      location.state !== null &&
      "scrollToCollectionImmediately" in location.state &&
      location.state.scrollToCollectionImmediately === true;

    collectionHeadingRef.current?.scrollIntoView({
      behavior: isProductReturn ? "instant" : "smooth",
      block: "start",
    });
  }, [isLoading, location.hash, location.state]);

  useEffect(() => {
    const drawerDialog = drawerRef.current;
    if (!drawerDialog) return;

    if (drawer === null) {
      if (drawerDialog.open) drawerDialog.close();
      return;
    }

    if (!drawerDialog.open) drawerDialog.showModal();
  }, [drawer]);

  const categories = useMemo(
    () =>
      [...new Set(products.map((product) => product.category))].sort(
        (left, right) => left.localeCompare(right),
      ),
    [products],
  );

  const appliedFilters = useMemo(
    () => readCollectionFilters(searchParams, categories, FILTER_COLORS),
    [categories, searchParams],
  );

  const colourCards = useMemo(() => createColourCards(products), [products]);

  const visibleCards = useMemo(
    () =>
      colourCards.filter((card) => {
        const matchesCategory =
          appliedFilters.categories.length === 0 ||
          appliedFilters.categories.includes(card.product.category);
        const matchesColor =
          appliedFilters.colors.length === 0 ||
          appliedFilters.colors.includes(card.apiColor);

        return matchesCategory && matchesColor;
      }),
    [appliedFilters, colourCards],
  );

  function applyFilters(): void {
    const nextSearchParams = updateCollectionFilterParams(
      searchParams,
      draftFilters,
    );
    const nextSearch = nextSearchParams.toString();

    navigate(
      {
        pathname: location.pathname,
        search: nextSearch ? `?${nextSearch}` : "",
        hash: "#collection-title",
      },
      { replace: true, preventScrollReset: true },
    );
    setDrawer(null);
    window.requestAnimationFrame(() => {
      collectionHeadingRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    });
  }

  function updateCategorySelection(category: string): void {
    setDraftFilters((currentFilters) => ({
      ...currentFilters,
      categories: toggleSelection(currentFilters.categories, category),
    }));
  }

  function updateColorSelection(color: string): void {
    setDraftFilters((currentFilters) => ({
      ...currentFilters,
      colors: toggleSelection(currentFilters.colors, color),
    }));
  }

  return (
    <div className={styles.siteShell}>
      <StorefrontHeader
        mode="home"
        onOpenFilters={() => {
          setDraftFilters(appliedFilters);
          setDrawer("filters");
        }}
        onOpenCart={() => setDrawer("cart")}
      />

      <main>
        <section className={styles.hero} aria-labelledby="hero-title">
          <div className={styles.heroCopy}>
            <h1 id="hero-title">
              <span className={styles.heroMoon}>
                <span className={styles.heroInitial}>M</span>oon
              </span>
              <span className={styles.heroCollection}>Collection</span>
            </h1>
          </div>
          <img
            className={styles.heroImage}
            src="/images/hero1.jpg"
            alt="A crescent moon above a mountain range beneath a starry sky"
          />
        </section>

        <section
          className={styles.collection}
          id="product-grid"
          aria-labelledby="collection-title"
        >
          <div className={styles.collectionHeading}>
            <h2
              ref={collectionHeadingRef}
              id="collection-title"
              className={styles.eyebrow}
            >
              Discover the collection
            </h2>
            <p className={styles.resultCount}>
              {isLoading
                ? "Loading pieces"
                : `${visibleCards.length} ${
                    visibleCards.length === 1 ? "piece" : "pieces"
                  }`}
            </p>
          </div>

          {isLoading && (
            <p className={styles.statusMessage} role="status">
              Loading the collection…
            </p>
          )}

          {!isLoading && loadError && (
            <div className={styles.errorMessage} role="alert">
              <p>The collection could not be loaded: {loadError}</p>
              <button
                className={styles.textButton}
                type="button"
                onClick={() => {
                  setIsLoading(true);
                  setLoadError(null);
                  setLoadAttempt((attempt) => attempt + 1);
                }}
              >
                Retry
              </button>
            </div>
          )}

          {!isLoading && !loadError && visibleCards.length === 0 && (
            <p className={styles.statusMessage}>
              No pieces match these filters.
            </p>
          )}

          {!isLoading && !loadError && visibleCards.length > 0 && (
            <div className={styles.productGrid}>
              {visibleCards.map((card) => (
                <Link
                  className={styles.productCard}
                  key={`${card.product.id}-${card.apiColor}`}
                  to={createProductDetailHref(
                    card.product.slug,
                    card.apiColor,
                    appliedFilters,
                  )}
                >
                  <img
                    className={styles.productImage}
                    src={card.imageUrl}
                    alt={`${card.product.name} in ${card.displayColor}`}
                    loading="lazy"
                  />
                  <div className={styles.productInformation}>
                    <div>
                      <h3>{card.product.name}</h3>
                      <p className={styles.productPrice}>
                        {formatEuroPrice(card.variant.retail_price)}
                      </p>
                    </div>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </section>
      </main>

      <StorefrontFooter />

      <dialog
        ref={drawerRef}
        className={`${styles.drawer} ${
          drawer === "cart" ? styles.drawerRight : styles.drawerLeft
        }`}
        aria-labelledby={drawer === "cart" ? "cart-title" : "filter-title"}
        onKeyDown={(event) => {
          if (event.key !== "Escape") return;
          event.preventDefault();
          setDrawer(null);
        }}
        onCancel={(event) => {
          event.preventDefault();
          setDrawer(null);
        }}
        onClick={(event) => {
          if (event.target === event.currentTarget) {
            setDrawer(null);
          }
        }}
      >
        {drawer === "filters" && (
          <div className={styles.drawerContent}>
            <div className={styles.drawerHeader}>
              <div>
                <p className={styles.eyebrow}>Refine your selection</p>
                <h2 id="filter-title">Filters</h2>
              </div>
              <button
                className={styles.closeButton}
                type="button"
                aria-label="Close filters"
                onClick={() => setDrawer(null)}
              >
                ×
              </button>
            </div>

            <div className={styles.filterSection}>
              <h3>Our Collection</h3>
              {categories.map((category) => (
                <label className={styles.checkboxRow} key={category}>
                  <input
                    type="checkbox"
                    checked={draftFilters.categories.includes(category)}
                    onChange={() => updateCategorySelection(category)}
                  />
                  <span>{displayCategory(category)}</span>
                </label>
              ))}
            </div>

            <div className={styles.filterSection}>
              <h3>Colour</h3>
              {COLOUR_OPTIONS.map((colourOption) => (
                <label
                  className={styles.checkboxRow}
                  key={colourOption.apiColor}
                >
                  <input
                    type="checkbox"
                    checked={draftFilters.colors.includes(
                      colourOption.apiColor,
                    )}
                    onChange={() => updateColorSelection(colourOption.apiColor)}
                  />
                  <span>{colourOption.label}</span>
                </label>
              ))}
            </div>

            <div className={styles.drawerActions}>
              <button
                className={styles.filterApplyButton}
                type="button"
                aria-label="Apply selected filters"
                title="Apply selected filters"
                onClick={applyFilters}
              >
                <SearchIcon />
              </button>
            </div>
          </div>
        )}

        {drawer === "cart" && (
          <CartContent onClose={() => setDrawer(null)} />
        )}
      </dialog>
    </div>
  );
}

export default StorefrontPage;
