import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ExportImportMenu, {
  type ExportImportMenuProps,
} from "./ExportImportMenu";

function renderMenu(overrides: Partial<ExportImportMenuProps> = {}) {
  const props: ExportImportMenuProps = {
    kind: "diagram",
    onExportPng: vi.fn(),
    onExportJpg: vi.fn(),
    onExportSvg: vi.fn(),
    onExportJson: vi.fn(),
    onImportJsonFile: vi.fn(),
    ...overrides,
  };
  const utils = render(<ExportImportMenu {...props} />);
  return { ...utils, props };
}

describe("ExportImportMenu", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders accessible Export and Import JSON buttons", () => {
    renderMenu({ kind: "diagram" });

    const exportBtn = screen.getByRole("button", { name: "Export diagram" });
    expect(exportBtn.getAttribute("aria-haspopup")).toBe("menu");
    expect(exportBtn.getAttribute("aria-expanded")).toBe("false");
    expect(
      screen.getByRole("button", { name: "Import diagram JSON" }),
    ).toBeTruthy();
  });

  it("opens menu with all four export options and invokes each handler", async () => {
    const { props } = renderMenu({ kind: "playbook" });

    const exportBtn = screen.getByRole("button", { name: "Export playbook" });

    // PNG
    fireEvent.click(exportBtn);
    expect(screen.getByRole("menu")).toBeTruthy();
    expect(screen.getAllByRole("menuitem")).toHaveLength(4);
    fireEvent.click(screen.getByRole("menuitem", { name: "Export PNG" }));
    await waitFor(() => {
      expect(props.onExportPng).toHaveBeenCalledTimes(1);
    });
    expect(screen.queryByRole("menu")).toBeNull();

    // JPG
    fireEvent.click(exportBtn);
    fireEvent.click(screen.getByRole("menuitem", { name: "Export JPG" }));
    await waitFor(() => {
      expect(props.onExportJpg).toHaveBeenCalledTimes(1);
    });

    // SVG
    fireEvent.click(exportBtn);
    fireEvent.click(screen.getByRole("menuitem", { name: "Export SVG" }));
    await waitFor(() => {
      expect(props.onExportSvg).toHaveBeenCalledTimes(1);
    });

    // JSON
    fireEvent.click(exportBtn);
    fireEvent.click(
      screen.getByRole("menuitem", { name: "Export JSON (Backup / Share)" }),
    );
    await waitFor(() => {
      expect(props.onExportJson).toHaveBeenCalledTimes(1);
    });
  });

  it("closes on Escape and click-outside", () => {
    renderMenu({ kind: "diagram" });

    const exportBtn = screen.getByRole("button", { name: "Export diagram" });
    fireEvent.click(exportBtn);
    expect(screen.getByRole("menu")).toBeTruthy();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(exportBtn);

    fireEvent.click(exportBtn);
    expect(screen.getByRole("menu")).toBeTruthy();
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("supports keyboard ArrowDown/ArrowUp navigation across menu items", () => {
    renderMenu({ kind: "diagram" });

    fireEvent.click(screen.getByRole("button", { name: "Export diagram" }));
    const menu = screen.getByRole("menu");
    const items = screen.getAllByRole("menuitem");

    items[0].focus();
    expect(document.activeElement).toBe(items[0]);

    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(document.activeElement).toBe(items[1]);

    fireEvent.keyDown(menu, { key: "ArrowUp" });
    expect(document.activeElement).toBe(items[0]);
  });

  it("invokes onImportJsonFile when a JSON file is selected", async () => {
    const { props } = renderMenu({ kind: "diagram" });

    const fileInput = screen.getByLabelText(
      "Select diagram JSON backup file",
    ) as HTMLInputElement;
    const mockFile = new File(['{"elder_export_version":1}'], "backup.json", {
      type: "application/json",
    });

    fireEvent.change(fileInput, { target: { files: [mockFile] } });

    await waitFor(() => {
      expect(props.onImportJsonFile).toHaveBeenCalledWith(mockFile);
    });
  });

  it("disables both buttons when disabled is true", () => {
    renderMenu({ kind: "diagram", disabled: true });

    expect(
      (screen.getByRole("button", { name: "Export diagram" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      (
        screen.getByRole("button", {
          name: "Import diagram JSON",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });
});
