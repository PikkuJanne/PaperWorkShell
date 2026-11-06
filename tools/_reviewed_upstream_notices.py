"""Exact public upstream attribution blobs reviewed for PWS-0801 and PWS-0802.

These immutable bytes contain published licence/author/provenance contacts,
including original vendor metadata and one official upstream schema contact.
They are not applicant information. Exact path/hash/category provenance is in
packaging/release-reviews/upstream-attribution-privacy-v1.json, citing the frozen
release reviews and official upstream schema PROVENANCE.json. The single additive
installer notice is bound separately by packaging/windows/inno-attribution-privacy-v1.json;
the historical PWS-0801 review and its source-epoch hash remain unchanged.

This module loads no paths or review files to grant permission. Only an explicit
repository-source context may recognize these exact bytes for email checking.
Core fixture checking remains strict; number/MRZ and document-identity checks
must remain active. Changed bytes and new addresses receive no allowance.
"""

from typing import Final

REVIEWED_UPSTREAM_NOTICE_EMAIL_DIGESTS: Final[frozenset[bytes]] = frozenset(
    {
        bytes.fromhex("03018f1893ab6446dfb220b9b953de78929ad63e9a4606591b05af142ce91cdd"),
        bytes.fromhex("08c135755dd589039470f1fdbb400daaabaaa50d0b366d19cebff4d22986baa1"),
        bytes.fromhex("0db603a5f4499f690c4425477ff664c166da325d3acc3b5a8d4de3db072443d0"),
        bytes.fromhex("1db8da07b436c68833c0673ffee3d9fcb2526047f3820b81661865dfedc79a1f"),
        bytes.fromhex("20b0f1b17ccd1bd5e8d763b1cdca4b095b8f0c037644b5ee18154536b7d8425e"),
        bytes.fromhex("2b1f5d615382a151af3eb71462ad17d89e2efee8fa7664058d35d3d9237ebb19"),
        bytes.fromhex("2eb469aebc37f20dda0960d9a5b03bfb8c0bb8a9d3fa5be0a356aa02fd0a8673"),
        bytes.fromhex("31204ebba071716f2cc06f798e6682e386c311218182e80cfae7e6f07cbaf63b"),
        bytes.fromhex("33fd641c9f3b0e0be64bc78fea9e94807674cdd70c48477599226cb8956565fe"),
        bytes.fromhex("34bda63a2fcc23c83f2e5364ccd394553b4baa505b1f79ded9cc2161c56a9b4c"),
        bytes.fromhex("4ae66e61d1c6593c84c47105b368d579bcee1af566dbb959ec63a0c48425dcf4"),
        bytes.fromhex("4d434596c0576fd1454e2de88f826d91518688fa2c3c46fff75c1f31471c4b01"),
        bytes.fromhex("4f7866a74802c6326f81faff59a56546b6aec2b10b91973e0e9308de95e79857"),
        bytes.fromhex("50b19507434ddb48ec9301755e7742e87807e04f56ab1886a3287861b0abe8e4"),
        bytes.fromhex("53cab2c771f7f7ffbf6a58ff759e5846b746e91d98dfe467d5145edf8cd7d306"),
        bytes.fromhex("5a5ee54c5001bbad1cdc1a57cc3dd4c42199b2da09d39c7ee41fab002d02967f"),
        bytes.fromhex("5e9f9325fd702bc4bcda27d7a78fea88a2a09fa39b4b15ac7b9b205e0863dc7e"),
        bytes.fromhex("5f216505c0f6ea3273caec89e766eef93cdeb7bbb0c429f9360116d7c938feeb"),
        bytes.fromhex("60b84ef09459007776c094984fd2559187e5adbc6e7b58696fa91077ff8654da"),
        bytes.fromhex("628b68bf60eb3bc1616342bc3545b63be2392e78bbebbc4c19f49675dd2ac8a7"),
        bytes.fromhex("62fd8327a27a4170361e097fbbfafaf68cdacadfdcad993ca251238112947a8f"),
        bytes.fromhex("642f29d7e28a211ccd2858e42b262fdc1c42e182d2682cf5d03429554fcdb2bc"),
        bytes.fromhex("65d86b8ec6b1cd8b8f6e64fd8710274ba00d6a3c520e00dfdcd1c3d2071f3305"),
        bytes.fromhex("68c5d236785edf75344cebe8b9bec36568bd35c96eb72cc67ffecd66c17dbd74"),
        bytes.fromhex("6d9f80349a29388841413ecb3d20473b6440768c2fdfa06d0456eb56d516daf3"),
        bytes.fromhex("75815e3bf6484201a3c3d17a1bbf10f2e8e3237f84df10a2357ea896db2a81d6"),
        bytes.fromhex("7c25493a9f64fed34d01445467341bda77bc1cdbeccbe33558659ef173fb9ff2"),
        bytes.fromhex("7c693ea746d879a27c3db8ae26131090d0cd89c36f79550ea6674b97e2da9a03"),
        bytes.fromhex("82f2f4427d6545ee5aaac4f0b80428da6cc8ba41c2cf5da3a03680ec327b9681"),
        bytes.fromhex("8614947dfc0ca1d21aad2f88b4400598262ab8c08aa87c4f7acc0c2de6e5619e"),
        bytes.fromhex("8b4e95f5cf0dc40269c99f7b787203ffe04ded245ca2427422c196efa2b2f42a"),
        bytes.fromhex("8bae002c25e723db7ee1f26afde680ae1a2b1a8f6b4b4b0fd65dc3becb090aae"),
        bytes.fromhex("8cddd6c73a885f697261c4d6d60e5cd814a2544fcd407504b52a5eb9b7cc3600"),
        bytes.fromhex("8f9651ba1fd4e35aa8b652958191d9df7a188a7264f547d577a8e82b4a9cda54"),
        bytes.fromhex("9283c7aad0a510531987c71302a8e3b1f0d9d1f1698f2c9bd00eb6b1679a91e2"),
        bytes.fromhex("935cf13e19f8c31b497d20b05d73623431a226b230c3599bc30fa3348979bc68"),
        bytes.fromhex("93679f4389d53b6835d89843f251844fb9bc455b35bab036d3c8e7abe497a47a"),
        bytes.fromhex("9a64a12138a2513b088b066a6b0c7cc33d87d48933f42638dd6e8203166882ed"),
        bytes.fromhex("a08799bb2385a3af99a9bbb7c746d0ad9ac7dd07be0fb1473f2f4d1dc3e11bd1"),
        bytes.fromhex("a8011369fd0cbcb28bcdcd6743ae37cbfb2e3d1842d0a9eee687c125afc642cd"),
        bytes.fromhex("b082a5f86775b2641f01bccb8b9c82908481de5f502bb254b1e13bfc2a9f303d"),
        bytes.fromhex("b2c560e0764523e0d3c2e7615c9379c71c25a9bfa1cc4d2f306ba433f037a7a4"),
        bytes.fromhex("ba6bceebcba0fdd35488477c2cca8c4632ce82c74dbfbc87d886ce6fc4433579"),
        bytes.fromhex("bab552307bdd03ed9a4cf7f8f1397c3d18d4dbee19ee576033284104785245be"),
        bytes.fromhex("bf543cdda11864477b8d2ca2aa9afc2aaa8f9133461b4d962ed6ec73646256a1"),
        bytes.fromhex("c110d3ea2ad77467ce0dcff7d3337e6c8be8049a5103f4b9bd5fd911a77972e5"),
        bytes.fromhex("c42285aa32440c3b5358fd4805b817cd8d6e1795111d2e4c547530be41896b2d"),
        bytes.fromhex("c6dbbf828498be844a89eaa3b84adbab3199e342eb5cb2ed2f0d4ba7ec0f38a3"),
        bytes.fromhex("c760d1f2e4614d58f2b91cd41b9826457989c09041be0219158b8f7f6d3f8763"),
        bytes.fromhex("cdbbd4ef3942363117366823fa89cd468fbed08d3e2ee5f7df8c38f2ba41fe2e"),
        bytes.fromhex("d106dcdb2a0b3087cb3a2380b9f00a0bbadc7eb835cd80408f94a2d78d69727d"),
        bytes.fromhex("d317c0b65f09d88bd4c4b268b76faab39368a8904fd86c39669c666b1c51d4a7"),
        bytes.fromhex("d5bbd7748a19e3fed6ea31242e8de9814e69a0125ae7758a7491057d5fe76f34"),
        bytes.fromhex("d7e3139972874dc8abdbbfce27ec1e6f942b28279dd7d27453630a8b4610e080"),
        bytes.fromhex("d89e31fed70f2cad9c6e91b787bf4c0fb680c631196b2a7215c81745aaf7fb7e"),
        bytes.fromhex("db16a04128171879c60708d171b88d97345a2dd20f9bfc173680a4497c73f704"),
        bytes.fromhex("de76bf802d4c0d06c13c2c7418d2f591bb878c4b011bb5648508d2b6b480f3c8"),
        bytes.fromhex("e32ff4e00d9d94930537635291da39e7e612703334bf6fde8c7f1686fe8a45a2"),
        bytes.fromhex("f4b133e25df1f86ad3ffea453aa0e613f0474f34778dbbb3e437e7b2724937d8"),
        bytes.fromhex("f7f9633cf9ff2f1333f3d7ce46973a8716a4d2a2815ad56f30d437d5fea7bafe"),
        bytes.fromhex("fc9c745d1883ff9f296a5b169f22eb2ee879f59a4608f20f5cb29d668f4e26f4"),
    }
)
