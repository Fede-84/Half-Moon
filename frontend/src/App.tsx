import {
  BrowserRouter,
  Link,
  Outlet,
  Route,
  Routes,
} from "react-router-dom";
import { CartProvider } from "./cart/CartContext";
import { AuthProvider } from "./features/auth/AuthProvider";
import {
  CustomerLoginPage,
  CustomerRegistrationPage,
  EmailVerificationPage,
  PasswordResetPage,
  StaffLoginPage,
} from "./features/auth/LoginForm";
import {
  CheckoutCancelledPage,
  CheckoutPage,
  CheckoutSuccessPage,
} from "./features/checkout/CheckoutPage";
import {
  AccountLayout,
  CustomerOrderDetailPage,
  CustomerOrderListPage,
} from "./features/account/AccountPages";
import { ClaimPOSOrderPage } from "./features/account/ClaimPOSOrderPage";
import {
  CustomerPOSPurchaseDetailPage,
  CustomerPOSPurchaseListPage,
} from "./features/account/CustomerPOSPurchasePages";
import { RequireCustomer } from "./features/auth/RequireCustomer";
import { RequireStaff } from "./features/auth/RequireStaff";
import { CustomerPOSDemoPage } from "./features/pos/CustomerPOSDemoPage";
import { PosPage } from "./features/pos/PosPage";
import { PosOrdersPage } from "./features/pos/PosOrdersPage";
import ProductDetailPage from "./ProductDetail";
import StorefrontPage from "./Storefront";

function StorefrontLayout() {
  return <Outlet />;
}

function PosLayout() {
  return <Outlet />;
}

function NotFoundPage() {
  return (
    <main>
      <h1>Page not found</h1>
      <Link to="/">Return to E-Commerce</Link>
    </main>
  );
}

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <CartProvider>
          <Routes>
            <Route path="/" element={<StorefrontLayout />}>
              <Route index element={<StorefrontPage />} />
              <Route path="products/:slug" element={<ProductDetailPage />} />
              <Route path="checkout" element={<CheckoutPage />} />
              <Route
                path="checkout/success"
                element={<CheckoutSuccessPage />}
              />
              <Route
                path="checkout/cancelled"
                element={<CheckoutCancelledPage />}
              />
            </Route>
            <Route
              path="/account"
              element={
                <RequireCustomer>
                  <AccountLayout />
                </RequireCustomer>
              }
            >
              <Route index element={<CustomerOrderListPage />} />
              <Route
                path="claim-pos-order"
                element={<ClaimPOSOrderPage />}
              />
              <Route
                path="pos-purchases"
                element={<CustomerPOSPurchaseListPage />}
              />
              <Route
                path="pos-purchases/:orderId"
                element={<CustomerPOSPurchaseDetailPage />}
              />
              <Route
                path="orders/:orderId"
                element={<CustomerOrderDetailPage />}
              />
            </Route>
            <Route
              path="/pos"
              element={
                <RequireStaff>
                  <PosLayout />
                </RequireStaff>
              }
            >
              <Route index element={<PosPage />} />
              <Route path="orders" element={<PosOrdersPage />} />
            </Route>
            <Route path="/pos-demo" element={<CustomerPOSDemoPage />} />
            <Route path="/login" element={<CustomerLoginPage />} />
            <Route path="/register" element={<CustomerRegistrationPage />} />
            <Route path="/verify-email" element={<EmailVerificationPage />} />
            <Route path="/reset-password" element={<PasswordResetPage />} />
            <Route path="/staff/login" element={<StaffLoginPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Routes>
        </CartProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
