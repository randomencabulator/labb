import { ComponentPage, componentMetadata } from "@/lib/ComponentPage";

export const metadata = componentMetadata("brand");

export default function Page() {
  return <ComponentPage slug="brand" />;
}
