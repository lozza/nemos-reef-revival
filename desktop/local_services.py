"""Explicit desktop replacements for optional Android SDK services."""


class LocalAdvertising:
    """No ad inventory or remote SDK; retain explicit desktop lifecycle state."""
    CLASS = "net/livingmobile/mopub/MoPubProxy"
    TAPJOY_CLASS = "net/livingmobile/sdr/tapjoy/TapjoyConnector"
    ADCOLONY_CLASS = 'net/livingmobile/adcolony/AdColonyProxy'

    def __init__(self):
        self.initialized = False
        self.offer_connect_requested = False
        self.offers_available = False
        self.offer_user_set = False
        self.offer_requests = 0
        self.video_initialized = False
        self.video_user_set = False
        self.video_configured = False
        self.video_listeners = []

    def invoke_video(self, method, descriptor, arguments):
        if method + descriptor == 'init(Landroid/app/Activity;Ljava/lang/String;Ljava/lang/String;)V':
            self.video_initialized = True
            return 0
        if method + descriptor == 'setCustomId(Ljava/lang/String;)V':
            self.video_user_set = True
            return 0  # No SDK, identifier retention, inventory or rewards.
        if method + descriptor == 'configure(Ljava/lang/String;Ljava/lang/String;)V':
            self.video_configured = True
            return 0
        if method + descriptor == 'addAdAvailabilityListener(JZ)V':
            self.video_listeners.append(arguments[0])
            return 0
        if method + descriptor == 'removeAdAvailabilityListener(JZ)V':
            if arguments[0] in self.video_listeners:
                self.video_listeners.remove(arguments[0])
            return 0
        if method + descriptor == 'isVideoAvailable(Ljava/lang/String;)Z':
            return 0  # Desktop has no video-ad inventory.
        if method + descriptor in ('onPauseActivity()V', 'onResumeActivity()V'):
            return 0
        raise RuntimeError(f'unimplemented desktop video-ad contract: {method}{descriptor}')

    def invoke_offer(self, method, descriptor, arguments):
        signature = method + descriptor
        if signature == "requestConnect(Landroid/app/Activity;Ljava/lang/String;Ljava/lang/String;)V":
            self.offer_connect_requested = True
        elif signature == "setUserID(Landroid/app/Activity;Ljava/lang/String;)V":
            self.offer_user_set = True
        elif signature == "showOffers(Landroid/app/Activity;Ljava/lang/String;)V":
            self.offer_requests += 1
        else:
            raise RuntimeError(f"unimplemented desktop offers contract: {signature}")
        return 0  # No SDK connection, inventory, identifier retention or rewards.

    def invoke(self, method, descriptor, arguments):
        if method + descriptor == 'isTabletDevice()Z':
            return 0  # Logical desktop viewport has a 540dp short edge.
        if method + descriptor in (
                'requestBannerAd(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;IIIIJ)I',
                'requestBannerAdTablet(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;IIIIJ)I',
                'requestFullScreenAd(Ljava/lang/String;Ljava/lang/String;J)I'):
            return 0  # No desktop ad inventory; report request not accepted.
        if method + descriptor in ('dismissFullScreenAd()V', 'presentFullScreenAd()V',
                                   'removeBannerAd()V', 'removeInterstitial()V'):
            return 0
        if method + descriptor == "setActivity(Landroid/app/Activity;)V":
            self.initialized = bool(arguments[0])
            return 0
        raise RuntimeError(f"unimplemented desktop advertising contract: {method}{descriptor}")


class LocalAnalytics:
    """Accept the recovered DMO interface locally; never create network traffic.

    This intentionally replaces the retired Android analytics SDK. It is not an
    implementation of Disney's analytics service. Unknown methods remain errors.
    """
    CLASS = "net/livingmobile/sdr/anl/DMOAnalyticsEngine"
    COMSCORE_CLASS = "net/livingmobile/sdr/anl/ComScoreAnalyticsEngine"
    GOOGLE_CLASS = "net/livingmobile/sdr/anl/GoogleAnalyticsEngine"
    KOCHAVA_CLASS = "net/livingmobile/sdr/kochava/KochavaService"

    def __init__(self):
        self.initialized = False
        self.network_requested = False
        self.restricted_tracking = True
        self.events_received = 0
        self.comscore_initialized = False
        self.google_initialized = False
        self.options_received = 0
        self.kochava_initialized = False

    def invoke_kochava(self, method, descriptor, arguments):
        if method + descriptor != "init(Landroid/app/Activity;Ljava/lang/String;)V":
            raise RuntimeError(f"unimplemented local Kochava contract: {method}{descriptor}")
        self.kochava_initialized = True
        return 0

    def invoke_google(self, method, descriptor, arguments):
        signature = method + descriptor
        if signature == "init(Ljava/lang/String;Landroid/app/Activity;I)V":
            self.google_initialized = True
        elif signature == "stop()V":
            self.google_initialized = False
        elif signature == "logEvent(Ljava/lang/String;Ljava/lang/String;)V":
            self.events_received += 1
        elif signature in ("setOptKey(Ljava/lang/String;ILjava/lang/String;)V", "setReferrer(Ljava/lang/String;)V"):
            self.options_received += 1
        else:
            raise RuntimeError(f"unimplemented local Google analytics contract: {signature}")
        return 0

    def invoke_comscore(self, method, descriptor, arguments):
        if method + descriptor != "start(Landroid/app/Activity;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)V":
            raise RuntimeError(f"unimplemented local ComScore contract: {method}{descriptor}")
        self.comscore_initialized = True
        return 0

    def invoke(self, method, descriptor, arguments):
        signature = method + descriptor
        if signature == "initialize(Landroid/app/Activity;Ljava/lang/String;Ljava/lang/String;)V":
            self.initialized = True
        elif signature == "setCanUseNetwork(Z)V":
            self.network_requested = bool(arguments[0])
        elif signature == "getCanUseNetwork()Z":
            return False  # There is no upload backend in the desktop replacement.
        elif signature == "setRestrictedTracking(Z)V":
            self.restricted_tracking = bool(arguments[0])
        elif signature == "getRestrictedTracking()Z":
            return self.restricted_tracking
        elif signature == "stop()V":
            self.initialized = False
        elif signature in ("logAppBackground()V", "logAppEnd()V", "logAppForeground()V",
                           "logEvent(Ljava/lang/String;Ljava/lang/String;)V"):
            self.events_received += 1
        elif signature == "flushAnalyticsQueue()V":
            pass  # Events are counted locally and are never queued for upload.
        else:
            raise RuntimeError(f"unimplemented local analytics contract: {signature}")
        return 0
