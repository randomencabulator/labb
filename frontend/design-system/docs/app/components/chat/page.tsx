import { ComponentPage, componentMetadata } from "@/lib/ComponentPage";

export const metadata = componentMetadata("chat");

export default function Page() {
  return <ComponentPage slug="chat" />;
}
