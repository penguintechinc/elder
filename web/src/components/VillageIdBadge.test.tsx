import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import VillageIdBadge from "./VillageIdBadge";

const SAMPLE_VID = "00000001-000001000000002a";

describe("VillageIdBadge", () => {
  let writeTextMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: writeTextMock },
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("renders a clickable /id/<villageId> link with full ID and origin title", () => {
    render(
      <MemoryRouter>
        <VillageIdBadge villageId={SAMPLE_VID} />
      </MemoryRouter>,
    );

    const link = screen.getByRole("link", { name: SAMPLE_VID });
    expect(link).toBeTruthy();
    expect(link.getAttribute("href")).toBe(`/id/${SAMPLE_VID}`);
    expect(link.getAttribute("title")).toBe(
      `Open ${window.location.origin}/id/${SAMPLE_VID}`,
    );
  });

  it("copies shareable permalink (${origin}/id/${villageId}) and resets check state after 2s", async () => {
    render(
      <MemoryRouter>
        <VillageIdBadge villageId={SAMPLE_VID} />
      </MemoryRouter>,
    );

    const copyLinkBtn = screen.getByRole("button", {
      name: "Copy Village ID link",
    });
    expect(copyLinkBtn.getAttribute("title")).toBe(
      "Copy shareable link (/id/...)",
    );

    await act(async () => {
      fireEvent.click(copyLinkBtn);
      await Promise.resolve();
    });

    expect(writeTextMock).toHaveBeenCalledTimes(1);
    expect(writeTextMock).toHaveBeenCalledWith(
      `${window.location.origin}/id/${SAMPLE_VID}`,
    );
    expect(copyLinkBtn.querySelector("svg.text-emerald-400")).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(2000);
    });

    expect(copyLinkBtn.querySelector("svg.text-emerald-400")).toBeNull();
  });

  it("copies raw villageId when clicking the secondary Copy ID button", async () => {
    render(
      <MemoryRouter>
        <VillageIdBadge villageId={SAMPLE_VID} />
      </MemoryRouter>,
    );

    const copyIdBtn = screen.getByRole("button", { name: "Copy Village ID" });

    await act(async () => {
      fireEvent.click(copyIdBtn);
      await Promise.resolve();
    });

    expect(writeTextMock).toHaveBeenCalledTimes(1);
    expect(writeTextMock).toHaveBeenCalledWith(SAMPLE_VID);
    expect(copyIdBtn.querySelector("svg.text-emerald-400")).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(2000);
    });

    expect(copyIdBtn.querySelector("svg.text-emerald-400")).toBeNull();
  });

  it("stops event propagation on link and copy button clicks inside clickable cards", async () => {
    const parentClick = vi.fn();

    render(
      <MemoryRouter>
        <div
          role="button"
          tabIndex={0}
          onClick={parentClick}
          onKeyDown={() => {}}
        >
          <VillageIdBadge villageId={SAMPLE_VID} />
        </div>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("link", { name: SAMPLE_VID }));

    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Copy Village ID link" }),
      );
      fireEvent.click(screen.getByRole("button", { name: "Copy Village ID" }));
      await Promise.resolve();
    });

    expect(parentClick).not.toHaveBeenCalled();
  });

  it("truncates long IDs in compact mode while preserving permalink href and copied values", async () => {
    render(
      <MemoryRouter>
        <VillageIdBadge villageId={SAMPLE_VID} compact />
      </MemoryRouter>,
    );

    const link = screen.getByRole("link");
    expect(link.textContent).toBe("000000...002a");
    expect(link.getAttribute("href")).toBe(`/id/${SAMPLE_VID}`);

    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Copy Village ID link" }),
      );
      await Promise.resolve();
    });
    expect(writeTextMock).toHaveBeenCalledWith(
      `${window.location.origin}/id/${SAMPLE_VID}`,
    );
  });

  it("renders plain text span when showLink is false", () => {
    render(
      <MemoryRouter>
        <VillageIdBadge villageId={SAMPLE_VID} showLink={false} />
      </MemoryRouter>,
    );

    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText(SAMPLE_VID)).toBeTruthy();
  });

  it("handles clipboard errors gracefully", async () => {
    const consoleErrorSpy = vi
      .spyOn(console, "error")
      .mockImplementation(() => {});
    writeTextMock.mockRejectedValueOnce(new Error("Clipboard denied"));

    render(
      <MemoryRouter>
        <VillageIdBadge villageId={SAMPLE_VID} />
      </MemoryRouter>,
    );

    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Copy Village ID link" }),
      );
      await Promise.resolve();
    });

    expect(consoleErrorSpy).toHaveBeenCalled();
  });
});
