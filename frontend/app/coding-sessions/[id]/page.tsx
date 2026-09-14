import { CodingSessions } from "@/components/coding-sessions";
export default async function Page({params}: {params: Promise<{id: string}>}) {
  const {id} = await params;
  return <CodingSessions sessionId={id} />;
}
