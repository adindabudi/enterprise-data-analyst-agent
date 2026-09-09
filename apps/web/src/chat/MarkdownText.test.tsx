import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MarkdownText } from "./MarkdownText";

describe("MarkdownText", () => {
  it("renders emphasis as text rather than literal asterisks", () => {
    render(<MarkdownText text="Risiko **hipertensi** meningkat." />);

    expect(screen.getByText("hipertensi").tagName).toBe("STRONG");
    expect(screen.queryByText(/\*\*/)).toBeNull();
  });

  it("renders a dash run as a list", () => {
    render(
      <MarkdownText text={"- Tekanan darah tinggi\n- Kolesterol tinggi"} />,
    );

    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("renders a citation as a safe external link", () => {
    render(<MarkdownText text="Sumber ([who.int](https://www.who.int/a))" />);
    const link = screen.getByRole("link", { name: "who.int" });

    expect(link).toHaveAttribute("href", "https://www.who.int/a");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("refuses a link target that is not http", () => {
    const { container } = render(
      <MarkdownText text="[klik](javascript:alert(1))" />,
    );

    expect(container.querySelector("a")).toBeNull();
  });

  it("keeps separate paragraphs apart", () => {
    const { container } = render(<MarkdownText text={"Satu.\n\nDua."} />);

    expect(container.querySelectorAll("p")).toHaveLength(2);
  });

  it("renders a pipe table instead of leaking the raw syntax", () => {
    const { container } = render(
      <MarkdownText
        text={
          "| ID | Pasien | SpO2 terbaru |\n|---:|---|---:|\n| 1018 | Amara Rivera | 89,8% |\n| 1059 | Felix Kowalski | 90,1% |"
        }
      />,
    );

    expect(screen.getAllByRole("columnheader")).toHaveLength(3);
    expect(screen.getAllByRole("row")).toHaveLength(3);
    expect(screen.getByRole("cell", { name: "Amara Rivera" })).toBeTruthy();
    expect(container.textContent).not.toContain("|---");
  });

  it("renders multiple tables that immediately follow section headings", () => {
    const { container } = render(
      <MarkdownText
        text={
          "### Ringkasan per RS\n| Region | RS | Occupancy |\n|---|---|---:|\n| OR | Riverside Hospital | 73,5% |\n\n### Rincian per jenis kamar\n| Region | RS | ICU |\n|---|---|---:|\n| OR | Riverside Hospital | 83,3% |"
        }
      />,
    );

    expect(container.querySelectorAll("table")).toHaveLength(2);
    expect(container.textContent).not.toContain("|---");
  });

  it("aligns table columns from the delimiter row", () => {
    const { container } = render(
      <MarkdownText text={"| a | b |\n|---:|:---:|\n| 1 | 2 |"} />,
    );
    const headers = container.querySelectorAll("th");

    expect(headers[0]?.style.textAlign).toBe("right");
    expect(headers[1]?.style.textAlign).toBe("center");
  });

  it("leaves a pipe line without a delimiter row as text", () => {
    const { container } = render(<MarkdownText text={"| bukan | tabel |"} />);

    expect(container.querySelector("table")).toBeNull();
  });
});
