import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AppProviders } from "../../app/providers/AppProviders";
import { createAppRouter } from "../../app/router";

describe("landing page", () => {
  it("renders business landing sections and CTAs", async () => {
    render(<AppProviders router={createAppRouter()} />);

    expect(await screen.findByText("Ask questions. Understand your business data.")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Open SQLSense" })[0]).toHaveAttribute("href", "/app");
    expect(screen.getByRole("link", { name: "See How It Works" })).toHaveAttribute("href", "#how-it-works");
    expect(screen.getByText("Supported capabilities")).toBeInTheDocument();
    expect(screen.getByText("Security and trust")).toBeInTheDocument();
  });
});
