import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { useTaskDetails } from "./useTaskDetails";

afterEach(() => vi.unstubAllGlobals());

it("keeps published outputs from earlier tasks when the active task is cleared", async () => {
  const taskIds = ["task_first_12345678", "task_second_12345678"];
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>((input) => {
      const path = input instanceof Request ? input.url : input.toString();
      const taskId = path.split("/")[3] ?? "";
      return Promise.resolve(
        new Response(
          JSON.stringify({
            artifacts: [
              {
                artifactId: `artifact-${taskId}`,
                version: 2,
                kind: "xlsx",
                sha256: "a".repeat(64),
                displayName: `${taskId}.xlsx`,
                sizeBytes: 2048,
              },
            ],
          }),
        ),
      );
    }),
  );
  const { result, rerender } = renderHook(
    ({ taskId }: { taskId: string | null }) =>
      useTaskDetails(taskId, false, taskIds),
    { initialProps: { taskId: taskIds[1] ?? null } },
  );
  await waitFor(() => {
    expect(result.current.artifacts).toHaveLength(2);
  });
  rerender({ taskId: null });
  expect(result.current.artifacts.map((artifact) => artifact.taskId)).toEqual(
    taskIds,
  );
});

it("preserves loaded files and reports a failed refresh instead of claiming empty outputs", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    new Response(
      JSON.stringify({
        artifacts: [
          {
            artifactId: "artifact-workbook12345678",
            version: 2,
            kind: "xlsx",
            sha256: "a".repeat(64),
            displayName: "occupancy.xlsx",
            sizeBytes: 2048,
          },
        ],
      }),
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
  const { result } = renderHook(() =>
    useTaskDetails("task_first_12345678", false),
  );
  await waitFor(() => {
    expect(result.current.artifacts).toHaveLength(1);
  });
  fetchMock.mockRejectedValue(new Error("temporarily unavailable"));
  act(() => {
    result.current.refresh();
  });
  await waitFor(() => {
    expect(result.current.artifactsStatus).toBe("error");
  });
  expect(result.current.artifacts[0]?.displayName).toBe("occupancy.xlsx");
});
