/*
 * Copyright The OpenTelemetry Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      https://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CollectorDetailPage } from "./collector-detail-page";
import { useCollectorComponent, useCollectorVersions } from "@/hooks/use-collector-data";
import type { CollectorComponent } from "@/types/collector";

vi.mock("@/hooks/use-collector-data", () => ({
  useCollectorComponent: vi.fn(),
  useCollectorVersions: vi.fn(),
}));

const mockComponentWithoutTelemetry: CollectorComponent = {
  id: "core-otlpreceiver",
  name: "otlpreceiver",
  ecosystem: "collector",
  type: "receiver",
  distribution: "core",
  display_name: "OTLP Receiver",
  description: "Receives OTLP telemetry.",
};

const mockComponentWithTelemetry: CollectorComponent = {
  ...mockComponentWithoutTelemetry,
  metrics: {
    "my.metric.name": {
      description: "A test metric",
      enabled: true,
      unit: "bytes",
      sum: {
        monotonic: true,
        value_type: "int",
        aggregation_temporality: "cumulative",
      },
    },
  },
};

function renderPage(initialPath = "/collector/components/core/otlpreceiver") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/collector/components/:distribution/:name" element={<CollectorDetailPage />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("CollectorDetailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useCollectorVersions).mockReturnValue({
      data: {
        versions: [{ version: "0.150.0", is_latest: true }],
      },
      loading: false,
      error: null,
    });
  });

  it("does not render Telemetry tab when component has no metrics", () => {
    vi.mocked(useCollectorComponent).mockReturnValue({
      data: mockComponentWithoutTelemetry,
      loading: false,
      error: null,
    });

    renderPage();

    expect(screen.queryByRole("tab", { name: /telemetry/i })).not.toBeInTheDocument();
  });

  it("renders Telemetry tab when component has metrics and displays content on click", async () => {
    const user = userEvent.setup();
    vi.mocked(useCollectorComponent).mockReturnValue({
      data: mockComponentWithTelemetry,
      loading: false,
      error: null,
    });

    renderPage();

    const telemetryTab = screen.getByRole("tab", { name: /telemetry/i });
    expect(telemetryTab).toBeInTheDocument();

    await user.click(telemetryTab);

    expect(screen.getByText("my.metric.name")).toBeInTheDocument();
  });
});
