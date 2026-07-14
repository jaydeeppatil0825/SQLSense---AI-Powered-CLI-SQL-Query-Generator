import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, type AnyRouter } from "@tanstack/react-router";
import { useState } from "react";
import { ErrorBoundary } from "./ErrorBoundary";
import { ThemeProvider } from "./theme";

export function AppProviders({ router }: { router: AnyRouter }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: false, refetchOnWindowFocus: false },
          mutations: { retry: false },
        },
      }),
  );

  return (
    <ErrorBoundary>
      <ThemeProvider>
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}
