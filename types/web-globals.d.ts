// Screen modules the browser scripts publish on window (UMD wrapper or direct assignment).
// Typed as `any` for now: the first step of gradual checking (see tsconfig.json).
interface Window {
  AccommodationTools: any;
  DiscoveryExperience: any;
  DiscoveryFlow: any;
  ProductTools: any;
  RecommendationPages: any;
  ReviewLab: any;
  TravelOffline: any;
  TravelTools: any;
  WorkspaceUX: any;
}
