import { describe, expect, it } from "vitest";
import { NAV_ITEMS } from "../layout/navigation";
import { paths, routeTree } from ".";

describe("router", () => {
  it("declares the public and application routes", () => {
    expect(Object.values(paths)).toEqual([
      "/",
      "/app",
      "/app/" + "query",
      "/app/" + "history",
      "/app/" + "database",
      "/app/" + "knowledge",
      "/app/" + "settings",
      "/app/" + "status",
      "/" + "query",
      "/" + "history",
      "/" + "database",
      "/" + "knowledge-base",
      "/" + "settings",
      "/" + "system-status",
    ]);
  });

  it("keeps navigation mapped to declared routes", () => {
    const declared = new Set(Object.values(paths));
    expect(NAV_ITEMS.every((item) => declared.has(item.to))).toBe(true);
    expect(routeTree.children?.length).toBe(8);
  });
});
