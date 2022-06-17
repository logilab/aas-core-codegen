/*
 * This code has been automatically generated by aas-core-codegen.
 * Do NOT edit or append.
 */

using CodeAnalysis = System.Diagnostics.CodeAnalysis;
using Regex = System.Text.RegularExpressions.Regex;
using System.Collections.Generic;  // can't alias
using System.Linq;  // can't alias

using Aas = dummyNamespace;

namespace dummyNamespace
{
    /// <summary>
    /// Verify that the instances of the meta-model satisfy the invariants.
    /// </summary>
    public static class Verification
    {
        [CodeAnalysis.SuppressMessage("ReSharper", "InconsistentNaming")]
        [CodeAnalysis.SuppressMessageAttribute("ReSharper", "IdentifierTypo")]
        [CodeAnalysis.SuppressMessage("ReSharper", "StringLiteralTypo")]
        private static Regex _constructMatchSomething()
        {
            var pattern = "^\\ud800\\udc00$";

            return new Regex(pattern);
        }

        private static readonly Regex RegexMatchSomething = _constructMatchSomething();

        public static bool MatchSomething(string text)
        {
            return RegexMatchSomething.IsMatch(text);
        }

        /// <summary>
        /// Hash allowed enum values for efficient validation of enums.
        /// </summary>
        internal static class EnumValueSet
        {

        }  // internal static class EnumValueSet

        [CodeAnalysis.SuppressMessage("ReSharper", "InconsistentNaming")]
        private static readonly Verification.Transformer _transformer = (
            new Verification.Transformer());

        private class Transformer
            : Visitation.AbstractTransformer<IEnumerable<Reporting.Error>>
        {

        }  // private class Transformer

        /// <summary>
        /// Verify the constraints of <paramref name="that" /> recursively.
        /// </summary>
        /// <param name="that">
        /// The instance of the meta-model to be verified
        /// </param>
        public static IEnumerable<Reporting.Error> Verify(Aas.IClass that)
        {
            foreach (var error in _transformer.Transform(that))
            {
                yield return error;
            }
        }
    }  // public static class Verification
}  // namespace dummyNamespace

/*
 * This code has been automatically generated by aas-core-codegen.
 * Do NOT edit or append.
 */
